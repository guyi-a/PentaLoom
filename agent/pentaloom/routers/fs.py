"""GET /fs/browse — 列出某个绝对路径下的子目录, 给前端 FolderPicker 用.
GET /fs/tree   — 递归列出某 sandbox/mount 路径下的目录树 (含文件), 给右栏 WorkspaceTree.
POST /fs/open  — 用系统默认 app 打开文件/目录 (前端用户主动点, LLM 不可见).

设计:
  - browse: 只列子目录, 不列文件 (做 mount 选择器用); 隐藏 . 开头的; 默认从 $HOME 开始
  - tree: 递归 (max_depth bound), 含文件; 默认 ignore .开头 + 一组重型目录 (.git/node_modules/...);
    sandbox/mount 鉴权 (path 必须落在 session scope 内); 用于 right panel 的文件树
  - open: 校验 path 落在 session 的 sandbox ∪ mounted_dirs 之内才放行;
    用 subprocess.Popen + 系统命令 (macOS open / Linux xdg-open / Windows explorer);
    不阻塞 — 失败/进程崩了前端不可知, 但本机桌面 app 不该把这种"启动后由 OS 接管"
    的事情包成完整状态机. M5+ 可加 exit code 抓取.
  - open 不暴露为 MCP 工具, 只服务 HTTP, 避免 LLM 用它做副作用.
"""

import os
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel

from pentaloom.infra.path_scope import resolve_session_scoped_path

router = APIRouter(prefix="/fs", tags=["fs"])

# 一次最多返回多少条 (防超大目录 DoS 前端)
MAX_ENTRIES = 500


class FsEntry(BaseModel):
    name: str
    path: str  # 绝对路径


class BrowseResponse(BaseModel):
    path: str            # 当前 (规范化后的绝对路径)
    parent: str | None   # 上一级 (根目录时为 None)
    home: str            # $HOME, 给前端"回家"按钮
    entries: list[FsEntry]
    truncated: bool      # 超过 MAX_ENTRIES 被截断


@router.get("/browse", response_model=BrowseResponse)
def browse(path: str | None = None, show_hidden: bool = False) -> BrowseResponse:
    home = str(Path.home())
    target = Path(path) if path else Path(home)

    if not target.is_absolute():
        raise HTTPException(400, f"path must be absolute: {path!r}")
    if not target.exists():
        raise HTTPException(404, f"path does not exist: {str(target)!r}")
    if not target.is_dir():
        raise HTTPException(400, f"path is not a directory: {str(target)!r}")

    try:
        target = target.resolve()
    except OSError as e:
        raise HTTPException(400, f"cannot resolve path: {e}") from e

    entries: list[FsEntry] = []
    truncated = False
    try:
        with os.scandir(target) as it:
            for de in it:
                if not show_hidden and de.name.startswith("."):
                    continue
                try:
                    if not de.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                entries.append(FsEntry(name=de.name, path=str(target / de.name)))
                if len(entries) >= MAX_ENTRIES:
                    truncated = True
                    break
    except PermissionError as e:
        raise HTTPException(403, f"permission denied: {e}") from e

    entries.sort(key=lambda e: e.name.lower())

    parent_path = str(target.parent) if target.parent != target else None

    return BrowseResponse(
        path=str(target),
        parent=parent_path,
        home=home,
        entries=entries,
        truncated=truncated,
    )


# ──── GET /fs/tree — 右栏 WorkspaceTree 用 ────────────────────────

# 默认 ignore 的目录名 (跟 . 开头一档默认隐藏). 经验值:
#   .git/.svn/.hg          版本控制
#   node_modules           前端依赖, 巨大
#   __pycache__/.pytest_cache/.ruff_cache/.mypy_cache  Python 工具缓存
#   .venv/venv             Python 虚拟环境
#   dist/build/out         构建产物
#   .next/.turbo/.nuxt     前端框架缓存
#   target                 Rust 构建产物
#   .DS_Store              macOS metadata (文件不是目录, 但顺手过滤)
DEFAULT_IGNORE_NAMES = frozenset({
    ".git", ".svn", ".hg",
    "node_modules",
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    ".venv", "venv",
    "dist", "build", "out",
    ".next", ".turbo", ".nuxt",
    "target",
    ".DS_Store",
})

TREE_MAX_DEPTH_HARD = 12   # 上限 — 防意外极深递归
TREE_MAX_DEPTH_DEFAULT = 8


class TreeNode(BaseModel):
    """文件树节点. children 仅 directory 含 (空数组也带 — 区分"读过/没子项"vs"未读")."""

    name: str
    path: str           # 绝对路径
    is_directory: bool
    children: list["TreeNode"] | None = None
    truncated: bool = False  # max_depth 触底时该目录的 children 没读


def _read_tree(
    target: Path,
    *,
    max_depth: int,
    show_hidden: bool,
    current_depth: int = 0,
) -> list[TreeNode]:
    """递归读 target 下的子项. 返排序后 (folder 优先 + alphabetic) list."""
    nodes: list[TreeNode] = []
    try:
        with os.scandir(target) as it:
            for de in it:
                if not show_hidden and de.name.startswith("."):
                    continue
                if de.name in DEFAULT_IGNORE_NAMES:
                    continue
                try:
                    is_dir = de.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                node = TreeNode(
                    name=de.name,
                    path=str(target / de.name),
                    is_directory=is_dir,
                )
                if is_dir:
                    if current_depth < max_depth:
                        # 子目录读取失败不挂掉整树, 给空 children + 后续修
                        try:
                            node.children = _read_tree(
                                Path(node.path),
                                max_depth=max_depth,
                                show_hidden=show_hidden,
                                current_depth=current_depth + 1,
                            )
                        except (OSError, PermissionError):
                            node.children = []
                    else:
                        node.truncated = True
                        node.children = None
                nodes.append(node)
    except PermissionError:
        # 父目录都没权限读, 返空就行
        return []

    # folder 优先 + alphabetic — 跟 krow 同款
    nodes.sort(key=lambda n: (not n.is_directory, n.name.lower()))
    return nodes


@router.get("/tree", response_model=TreeNode)
async def tree(
    session_id: str = Query(..., description="当前 session id (鉴权)"),
    path: str = Query(..., description="树根, 必须是 sandbox 或某个 mount 子树"),
    max_depth: int = Query(
        TREE_MAX_DEPTH_DEFAULT,
        ge=1,
        le=TREE_MAX_DEPTH_HARD,
        description="递归深度上限 (防大项目卡)",
    ),
    show_hidden: bool = Query(False, description="是否显示 . 开头的文件"),
) -> TreeNode:
    """递归出 path 下的目录树, 给右栏 WorkspaceTree 用.

    - path 必须落在 session 的 sandbox ∪ mounted_dirs 内 (resolve_session_scoped_path 校)
    - DEFAULT_IGNORE_NAMES 默认过滤 .git / node_modules / __pycache__ 等 (即使 show_hidden=True 也过滤)
    - 排序: folder 优先 + alphabetic
    - truncated=True 表示该 dir 触 max_depth, 还有未读的子项 (前端可显示 "..." 提示)
    """
    target = await resolve_session_scoped_path(session_id, path)
    if not target.is_dir():
        raise HTTPException(400, f"path is not a directory: {target}")

    children = _read_tree(
        target,
        max_depth=max_depth,
        show_hidden=show_hidden,
    )
    return TreeNode(
        name=target.name or str(target),
        path=str(target),
        is_directory=True,
        children=children,
    )


# ──── POST /fs/open ───────────────────────────────────────────────


class OpenPathBody(BaseModel):
    session_id: str
    path: str          # 必须落在 session sandbox ∪ mounted_dirs 内
    reveal: bool = False  # True = 在文件管理器中"显示"该文件 (而非用 app 打开)


class OpenPathResponse(BaseModel):
    opened: str  # 实际打开的规范化绝对路径


def _platform_open_cmd(path: Path, *, reveal: bool) -> list[str]:
    """按平台返回打开命令. reveal=True 时定位到文件 (Finder/Explorer 选中).

    Linux 没"reveal"原生概念 — 退化为打开父目录, 文件不选中.
    """
    p = str(path)
    if sys.platform == "darwin":
        return ["open", "-R", p] if reveal else ["open", p]
    if sys.platform.startswith("linux"):
        return ["xdg-open", str(path.parent) if reveal else p]
    if sys.platform == "win32":
        return ["explorer", "/select,", p] if reveal else ["explorer", p]
    raise HTTPException(500, f"unsupported platform: {sys.platform}")


@router.post("/open", response_model=OpenPathResponse)
async def open_path(body: OpenPathBody) -> OpenPathResponse:
    """用系统默认 app 打开 path. 前端用户主动点才触发, LLM 无此工具.

    安全: resolve_session_scoped_path 校 path 落在 session 的 sandbox / mounted_dirs 内.
    """
    target = await resolve_session_scoped_path(body.session_id, body.path)
    cmd = _platform_open_cmd(target, reveal=body.reveal)
    try:
        # start_new_session 让被启动的 GUI app 脱离 uvicorn 进程组, ctrl+c 不会带挂.
        subprocess.Popen(cmd, start_new_session=True)
    except (OSError, FileNotFoundError) as e:
        raise HTTPException(500, f"failed to spawn opener: {e}") from e

    logger.info(f"/fs/open sid={body.session_id} target={target} reveal={body.reveal}")
    return OpenPathResponse(opened=str(target))

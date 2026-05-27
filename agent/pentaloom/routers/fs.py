"""GET /fs/browse — 列出某个绝对路径下的子目录, 给前端 FolderPicker 用.
POST /fs/open — 用系统默认 app 打开文件/目录 (前端用户主动点, LLM 不可见).

设计:
  - browse: 只列子目录, 不列文件 (我们只挂目录); 隐藏 . 开头的; 默认从 $HOME 开始
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

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

from pentaloom.config import get_settings
from pentaloom.crud import chat_session as crud_chat
from pentaloom.infra.db import AsyncSessionLocal

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


# ──── POST /fs/open ───────────────────────────────────────────────


class OpenPathBody(BaseModel):
    session_id: str
    path: str          # 必须落在 session sandbox ∪ mounted_dirs 内
    reveal: bool = False  # True = 在文件管理器中"显示"该文件 (而非用 app 打开)


class OpenPathResponse(BaseModel):
    opened: str  # 实际打开的规范化绝对路径


def _is_within(target: Path, root: Path) -> bool:
    """target 是否在 root 子树下 (含 root 本身). 已 resolve 过, 不再处理 symlink."""
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


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

    安全: 只允许打开 session 的 sandbox 子树 + 用户已挂载的目录子树.
    错配的根 (e.g. mounted_dirs 含 "/") 等于本机任意文件可执行 — 这是 mounted_dirs
    校验层的责任 (chat._validate_mounted_dirs 已禁非 dir/不存在, 但没禁 "/" 顶层).
    M5+ 加 deny-list.
    """
    settings = get_settings()

    async with AsyncSessionLocal() as db:
        row = await crud_chat.get_chat_session(db, body.session_id)
    if row is None:
        raise HTTPException(404, f"session {body.session_id!r} not found")

    raw = Path(body.path)
    if not raw.is_absolute():
        raise HTTPException(400, f"path must be absolute: {body.path!r}")
    try:
        target = raw.resolve(strict=False)
    except OSError as e:
        raise HTTPException(400, f"cannot resolve path: {e}") from e
    if not target.exists():
        raise HTTPException(404, f"path not found: {target}")

    sandbox = settings.sandbox_dir_for(body.session_id).resolve()
    mounted_roots: list[Path] = []
    for d in row.mounted_dirs:
        try:
            mounted_roots.append(Path(d).resolve(strict=False))
        except OSError:
            continue
    allowed_roots = [sandbox, *mounted_roots]
    if not any(_is_within(target, root) for root in allowed_roots):
        logger.warning(
            f"/fs/open denied sid={body.session_id} target={target} "
            f"allowed={[str(r) for r in allowed_roots]}"
        )
        raise HTTPException(403, "path outside allowed roots")

    cmd = _platform_open_cmd(target, reveal=body.reveal)
    try:
        # start_new_session 让被启动的 GUI app 脱离 uvicorn 进程组, ctrl+c 不会带挂.
        subprocess.Popen(cmd, start_new_session=True)
    except (OSError, FileNotFoundError) as e:
        raise HTTPException(500, f"failed to spawn opener: {e}") from e

    logger.info(f"/fs/open sid={body.session_id} target={target} reveal={body.reveal}")
    return OpenPathResponse(opened=str(target))

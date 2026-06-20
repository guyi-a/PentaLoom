"""按 session 校 path 落在 sandbox ∪ mounted_dirs 子树内.

routers/fs.py 的 open + routers/preview.py 共用. 抽这里防两套安全逻辑漂移.

设计:
  - resolve_session_scoped_path: 一次完成"读 session + resolve path + relative_to 校",
    返 resolve 后的 Path. 越权 / 不存在 / 不合法 raise HTTPException.
  - 内部用 Path.resolve() 跟随 symlink, 然后 relative_to(allowed_root) 强校 — 防符号
    链接指向 sandbox 外被绕开 (GPT review 提的, Path.resolve(strict=False) 不够).
  - session_id 允许为空: 跳过 session 查询, 只校 weaver_root. 用于 app 详情弹窗在
    无活跃 session 时预览 weaver 产物源码.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from loguru import logger

from pentaloom.config import get_settings
from pentaloom.crud import chat_session as crud_chat
from pentaloom.infra.db import AsyncSessionLocal


def _is_within(target: Path, root: Path) -> bool:
    """target 是否在 root 子树下 (含 root 本身). 调方应已 resolve."""
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


async def resolve_session_scoped_path(
    session_id: str,
    raw_path: str,
    *,
    require_file: bool = False,
) -> Path:
    """校 raw_path 落在 session 的 sandbox ∪ mounted_dirs ∪ weaver_root 内, 返 resolve 后的 Path.

    session_id 可以为空字符串 — 此时跳过 session 查询, 只校 weaver_root.
    (weaver 产物跨 session 共享, app 详情弹窗在无活跃 session 时仍需预览源码.)

    raise HTTPException:
      - 400 path 不是 absolute / resolve 失败
      - 404 session 不存在 / path 不存在 / require_file=True 但是目录
      - 403 path 在 allowed_roots 外 (含 symlink 越界)
    """
    raw = Path(raw_path)
    if not raw.is_absolute():
        raise HTTPException(400, f"path must be absolute: {raw_path!r}")

    try:
        # resolve 跟随 symlink — 越界检查必须在 resolve 后做
        target = raw.resolve(strict=False)
    except OSError as e:
        raise HTTPException(400, f"cannot resolve path: {e}") from e

    if not target.exists():
        raise HTTPException(404, f"path not found: {target}")

    if require_file and target.is_dir():
        raise HTTPException(400, f"expected file, got directory: {target}")

    settings = get_settings()
    # weaver 产物根 — 跨 session 共享, 无论有没有 session 都放行
    weaver_root = (settings.data_dir / "weaver").resolve(strict=False)

    if session_id:
        # 有 session: sandbox ∪ mounted_dirs ∪ weaver_root
        async with AsyncSessionLocal() as db:
            row = await crud_chat.get_chat_session(db, session_id)
        if row is None:
            raise HTTPException(404, f"session {session_id!r} not found")
        sandbox = settings.sandbox_dir_for(session_id).resolve()
        mounted_roots: list[Path] = []
        for d in row.mounted_dirs:
            try:
                mounted_roots.append(Path(d).resolve(strict=False))
            except OSError:
                continue
        allowed_roots = [sandbox, *mounted_roots, weaver_root]
    else:
        # 无 session (app 详情弹窗无活跃 thread): 只放行 weaver_root
        allowed_roots = [weaver_root]

    if not any(_is_within(target, root) for root in allowed_roots):
        logger.warning(
            f"path_scope deny sid={session_id!r} target={target} "
            f"allowed={[str(r) for r in allowed_roots]}"
        )
        raise HTTPException(403, "path outside allowed roots")

    return target

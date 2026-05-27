"""GET /fs/browse — 列出某个绝对路径下的子目录, 给前端 FolderPicker 用.

设计:
  - 只列子目录, 不列文件 (我们只挂目录)
  - 隐藏 . 开头的 (.git/.venv 之类), 但保留可在 query 里开
  - 没传 path → 默认从 $HOME 开始
  - 拒绝非绝对路径 / 不存在 / 不是 dir / 没读权限
"""

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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

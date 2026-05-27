"""PentaLoom 后端入口.

    python main.py                         # 默认
    PENTALOOM_DEBUG=true python main.py    # 热重载

Electron Main 后续 spawn 也是这条命令 (产版换 Nuitka binary).
"""

import uvicorn

from pentaloom.config import get_settings


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "pentaloom.server:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        reload_dirs=["pentaloom"] if settings.debug else None,
        reload_excludes=["pentaloom-data/**"] if settings.debug else None,
        access_log=settings.access_log,
    )

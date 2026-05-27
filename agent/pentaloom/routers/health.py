"""健康检查 / 服务元信息."""

from fastapi import APIRouter

from pentaloom import __version__ as pentaloom_version
from pentaloom.config import get_settings

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    s = get_settings()
    return {
        "status": "ok",
        "version": pentaloom_version,
        "model": s.model,
        "data_dir": str(s.data_dir),
    }

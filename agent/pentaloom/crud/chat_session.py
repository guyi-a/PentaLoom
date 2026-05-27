"""ChatSession (用户视角的会话) CRUD.

设计要点:
  - session_id 由调用方生成 (UUID), 不在这里发, 因为 LoomPool / chat 路由
    也需要拿同一个 sid 用.
  - mounted_dirs 在 create 时初始化, 之后可以通过 add_mounted_dir 增量加
    (阶段 2 的 request_workspace_dir 工具用).
  - touch_last_active 每次 /chat 续聊后刷, 给前端做"最近会话"排序.
  - 沙箱目录由调用方负责 mkdir (路由层); crud 只管 db 状态.
"""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from pentaloom.models.session import ChatSession


async def create_chat_session(
    db: AsyncSession, *, session_id: str, mounted_dirs: list[str],
    title: str | None = None,
) -> ChatSession:
    row = ChatSession(
        session_id=session_id, mounted_dirs=list(mounted_dirs), title=title,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def get_chat_session(
    db: AsyncSession, session_id: str
) -> ChatSession | None:
    result = await db.execute(
        select(ChatSession).where(ChatSession.session_id == session_id)
    )
    return result.scalar_one_or_none()


async def touch_last_active(db: AsyncSession, session_id: str) -> None:
    await db.execute(
        update(ChatSession)
        .where(ChatSession.session_id == session_id)
        .values(last_active_at=func.now())
    )
    await db.commit()


async def add_mounted_dir(
    db: AsyncSession, *, session_id: str, path: str
) -> list[str]:
    """挂载一个新目录到 session, 已存在则幂等. 返回更新后的完整挂载列表."""
    row = await get_chat_session(db, session_id)
    if row is None:
        raise ValueError(f"session {session_id} not found")
    if path not in row.mounted_dirs:
        # JSON 字段 in-place mutate SQLA 检测不到, 必须赋新 list 触发 dirty
        row.mounted_dirs = [*row.mounted_dirs, path]
        await db.commit()
        await db.refresh(row)
    return list(row.mounted_dirs)


async def set_mounted_dirs(
    db: AsyncSession, session_id: str, dirs: list[str]
) -> list[str]:
    """整体替换 mounted_dirs. 调用方负责校验/去重/标准化."""
    row = await get_chat_session(db, session_id)
    if row is None:
        raise ValueError(f"session {session_id} not found")
    row.mounted_dirs = list(dirs)
    await db.commit()
    await db.refresh(row)
    return list(row.mounted_dirs)


async def list_chat_sessions(db: AsyncSession) -> list[ChatSession]:
    result = await db.execute(
        select(ChatSession).order_by(ChatSession.last_active_at.desc())
    )
    return list(result.scalars().all())

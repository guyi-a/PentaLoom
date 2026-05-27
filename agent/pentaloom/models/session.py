"""Session 相关表.

两类:
  - ChatSession: PentaLoom 业务表, 一行 = 一个用户会话, 存 cwd / title / 时间戳
  - SessionEntry / SessionSummary / SessionMtime: Claude Agent SDK 的
    SessionStore 镜像, opaque JSON blob, 跟 SDK 的内部 session 概念绑.

两者共享同一个 session_id (UUID), 但语义不同 — ChatSession 是产品层"会话",
镜像表是 SDK 的写入流。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from pentaloom.infra.db import Base


class ChatSession(Base):
    """用户视角的会话: 决定挂载的目录 + 标题 + 时间戳.

    多挂载: mounted_dirs 是用户授权过的目录列表 (可空, 空就只能聊不能动文件).
    主 cwd 永远是 sandbox_dir (data_dir/sandboxes/<sid>/), agent 私有中间产物落这里;
    用户挂载的目录走 SDK 的 add_dirs, 不当 cwd 用 — 这样"没挂载"和"挂载了 N 个"
    的代码路径一致.
    """

    __tablename__ = "chat_sessions"

    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    mounted_dirs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class SessionEntry(Base):
    """单条 session entry (SDK 写一次, 这边镜像一行)."""

    __tablename__ = "session_entries"
    __table_args__ = (
        Index("ix_session_entries_lookup", "project_key", "session_id", "subpath"),
    )

    project_key: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    subpath: Mapped[str] = mapped_column(String, primary_key=True, default="")
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)

    uuid: Mapped[str | None] = mapped_column(String, nullable=True)
    type: Mapped[str | None] = mapped_column(String, nullable=True)
    timestamp: Mapped[str | None] = mapped_column(String, nullable=True)
    entry_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    mtime_ms: Mapped[int] = mapped_column(Integer)


class SessionSummary(Base):
    """每个主 session (subpath='') 的 fold 出来的摘要."""

    __tablename__ = "session_summaries"

    project_key: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    mtime_ms: Mapped[int] = mapped_column(Integer)
    data_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SessionMtime(Base):
    """每个 (project_key, session_id, subpath) 的最新 mtime."""

    __tablename__ = "session_mtimes"

    project_key: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    subpath: Mapped[str] = mapped_column(String, primary_key=True, default="")
    mtime_ms: Mapped[int] = mapped_column(Integer)

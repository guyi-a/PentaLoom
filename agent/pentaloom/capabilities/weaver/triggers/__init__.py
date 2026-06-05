"""Weaver app 触发器 — M16 Phase E.

schedule (cron) 和 watch (fs event) 两类异步触发器统一管理. 实现复用
service_registry 的 in-memory singleton + asyncio.Lock 模式.

公开入口都从 TriggerRegistry 走, 模块只暴露 trigger_registry() 拿单例.
"""

from pentaloom.capabilities.weaver.triggers.registry import (
    TriggerRegistry,
    trigger_registry,
)

__all__ = ["TriggerRegistry", "trigger_registry"]

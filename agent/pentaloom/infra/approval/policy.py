"""三档审批策略 — default / auto / full_access.

调用顺序 (在 tools/workspace.py make_can_use_tool 闭包里):
  1. destructive 拦截 (跨所有模式, 在 policy 之前判, 命中直接走人工审批)
  2. policy.should_auto_approve(tool_name, tool_input)
     - default: 全部 (False, None) → 落回原 allowlist / 人工审批流程
     - auto:    Bash 且 harmless → (True, "harmless"); 其他 → LLM 兜底 (现 stub)
     - full_access: (True, "full_access")
  3. 没自动批的, 落回原 allowlist_key 命中 / register Future 流程

reason 字段:
  - 自动批 (True): 写 source 标签 (审计用), e.g. "harmless" / "full_access" / "llm_classifier"
  - 不自动批 (False): None 或简短说明 (e.g. "no_policy_match")
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

from .classifier import classify_with_llm
from .cmd_classify import is_harmless_command


ApprovalMode = Literal["default", "auto", "full_access"]
APPROVAL_MODES: tuple[str, ...] = ("default", "auto", "full_access")


class ApprovalModeRef:
    """单字段引用容器, 让 make_can_use_tool 闭包跟 _Entry 共享 mode 引用.

    str 是值类型, closure 直接闭住会把当时的值快照下来, 之后改不到. dict 是
    天然引用类型 (hitl_allowlists 用的就是这条路), 但单字段 dict 不直观.
    用 __slots__ 小 class 包一层, 改 ref.value 立刻被 closure 读到.
    """
    __slots__ = ("value",)

    def __init__(self, value: str = "default") -> None:
        self.value = value

    def __repr__(self) -> str:
        return f"ApprovalModeRef(value={self.value!r})"


class ApprovalPolicy(ABC):
    """决定一个工具调用要不要自动批准.

    destructive 检测在调用本接口之前已经做完, 子类实现时假设传进来的工具调用
    不在 destructive deny list. 子类只决定 "要不要让用户拍板".
    """

    @abstractmethod
    async def should_auto_approve(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        *,
        workspace_dir: str | None = None,
    ) -> tuple[bool, str | None]:
        """返 (approved, reason).

        approved=True  : 直接放行, 不进人工审批流
        approved=False : 落回原 allowlist 命中 / 人工审批流
        """
        ...


class DefaultPolicy(ApprovalPolicy):
    """保守模式 — 不做任何自动批, 全部走人工审批 (现状行为)."""

    async def should_auto_approve(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        *,
        workspace_dir: str | None = None,
    ) -> tuple[bool, str | None]:
        return (False, None)


class AutoPolicy(ApprovalPolicy):
    """中庸模式 — Bash 无害命令静默, 其他工具走 LLM 兜底裁判.

    LLM 失败 (没 key / 超时 / API 错) 一律 fall back 到 (False, reason) 偏严,
    用户落回人工审批.
    """

    async def should_auto_approve(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        *,
        workspace_dir: str | None = None,
    ) -> tuple[bool, str | None]:
        if tool_name == "Bash":
            cmd = (tool_input.get("command") or "").strip()
            if cmd and is_harmless_command(cmd):
                return (True, "harmless")
        # harmless 不命中 (Bash 复杂命令 / 非 Bash 工具) → LLM 兜底裁判.
        # 失败一律走人工审; 不重试 — 用户等 15s 再被弹一次比 30s 体验差.
        return await classify_with_llm(
            tool_name, tool_input, workspace_dir=workspace_dir,
        )


class FullAccessPolicy(ApprovalPolicy):
    """全自动模式 — 所有非 destructive 工具直接放行.

    destructive 已在调用本接口前由外层拦截, 这里直接 (True, "full_access").
    """

    async def should_auto_approve(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        *,
        workspace_dir: str | None = None,
    ) -> tuple[bool, str | None]:
        return (True, "full_access")


_REGISTRY: dict[str, type[ApprovalPolicy]] = {
    "default": DefaultPolicy,
    "auto": AutoPolicy,
    "full_access": FullAccessPolicy,
}


def get_policy(mode: str) -> ApprovalPolicy:
    """从 mode 字符串拿对应 policy 实例. 未知 mode fallback 到 DefaultPolicy."""
    cls = _REGISTRY.get(mode, DefaultPolicy)
    return cls()

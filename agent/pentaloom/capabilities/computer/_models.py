"""computer-use 工具返回值的 Pydantic 模型.

跟 browser_bridge 那边一样, 工具返回 dict 序列化成 JSON 给 LLM. 模型在这里
定义, 工具层 model_dump_json() 后塞进 SDK tool_result.
"""

from __future__ import annotations

from pydantic import BaseModel


class PermissionStatus(BaseModel):
    """computer_permissions 返回."""

    trusted: bool                # AXIsProcessTrusted()
    host_process: str            # 启动 python 的 GUI 进程 (Terminal / iTerm / Electron 等)
    host_binary: str             # 启动 python 的 binary 绝对路径
    prompt_triggered: bool       # 这一次调用是否触发了系统授权弹窗
    message: str                 # 给 LLM 看的状态描述


class AppInfo(BaseModel):
    """computer_apps / list 的一项."""

    pid: int
    name: str
    bundle: str
    active: bool                 # 是否前台


class AXElement(BaseModel):
    """AX 树里一个元素的摘要. snapshot 用. 不带 children — 树结构靠 nesting.

    LLM 用 index 定位; index 是这次 snapshot 里的序号 (从 0 起, 深度优先扁平化).
    瞬态 — 操作后页面变了就要重新 snapshot.
    """

    index: int
    role: str                    # AXButton / AXTextField / AXMenuItem ...
    subrole: str | None = None
    role_description: str | None = None
    title: str | None = None
    value: str | None = None
    description: str | None = None
    help: str | None = None
    enabled: bool | None = None
    focused: bool | None = None
    depth: int                   # 在树里的深度


class SnapshotResult(BaseModel):
    """computer_snapshot 返回."""

    app_name: str
    pid: int
    elapsed_ms: int
    total_elements: int
    interactive_elements: int    # 可交互的 (button / textfield / menuitem 等) 数量
    elements: list[AXElement]    # 扁平化后的元素列表, 按深度优先序


class ActionResult(BaseModel):
    """computer_press / set_value / focus / key / menu 等动作的结果."""

    action: str
    target_description: str      # 操作的元素的可读描述
    success: bool
    message: str


class MenuItem(BaseModel):
    """menu path 解析时返回的一项, 给 LLM debug 用."""

    title: str
    enabled: bool

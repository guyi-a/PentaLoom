"""computer-use 工具返回值的 Pydantic 模型.

跟 browser_bridge 那边一样, 工具返回 dict 序列化成 JSON 给 LLM. 模型在这里
定义, 工具层 model_dump_json() 后塞进 SDK tool_result.
"""

from __future__ import annotations

from pydantic import BaseModel


class SubsystemPermission(BaseModel):
    """单类 TCC 权限的状态. M9 起 Accessibility / Screen Recording 分开报."""

    trusted: bool                # 当前是否已授权
    prompt_triggered: bool       # 这次调用是否触发了系统授权弹窗
    message: str                 # 给 LLM 看的状态描述 (含引导文案)


class PermissionsReport(BaseModel):
    """computer_permissions 返回. 双权限合并报告.

    host_process / host_binary 共用 — Accessibility 和 Screen Recording 都是给
    "启动 python 的 GUI 宿主进程" (Terminal / iTerm / VSCode / Electron) 授权的,
    宿主切了就要重新授权两类.
    """

    host_process: str
    host_binary: str
    accessibility: SubsystemPermission         # AX 树 / mouse / keyboard CGEvent 都依赖这个
    screen_recording: SubsystemPermission      # screenshot 依赖这个 (跟 Accessibility 独立 TCC)


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
    """computer_press / set_value / focus / key / menu / mouse_* / paste 等动作的结果."""

    action: str
    target_description: str      # 操作的元素的可读描述
    success: bool
    message: str


class MenuItem(BaseModel):
    """menu path 解析时返回的一项, 给 LLM debug 用."""

    title: str
    enabled: bool


class PxSize(BaseModel):
    """像素尺寸. screenshot 用 — 区分物理 / 逻辑 / 实际编码三套尺寸."""

    w: int
    h: int


class PxOffset(BaseModel):
    """像素偏移. 给多屏 displays 标 logical_origin 用."""

    x: int
    y: int


class DisplayInfo(BaseModel):
    """单个屏幕的描述. 多屏环境下 LLM 看截图后要换算坐标定位 mouse 该点哪个屏.

    坐标系: mouse 用的 top-left logical (主屏左上 = (0, 0)). 副屏 origin 是它的
    左上角相对主屏左上角的位移 (例: 副屏在主屏右侧, origin=(1728, 0)).
    """

    is_main: bool
    logical_origin: PxOffset
    logical_size: PxSize
    scale: float                  # Retina = 2.0, 普通屏 = 1.0


class ScreenshotResult(BaseModel):
    """computer_screenshot 返回.

    LLM 必须自己换算坐标: mouse 用 top-left logical 像素, 截图返回的是 desktop union
    缩放后的物理像素. 多屏时 image 横跨所有屏, displays 列表说明每个屏的 logical 范围.

    公式 (假设所有屏同 scale, 实践 99% 满足):
      logical_x = image_x * (desktop_logical.w / scaled_px.w)
      logical_y = image_y * (desktop_logical.h / scaled_px.h)
    然后看 displays 判定 logical_x 落在哪个屏 (origin.x ≤ logical_x < origin.x + size.w).
    """

    image_b64: str
    format: str
    quality: int | None
    scale_applied: float
    target: str
    physical_px: PxSize           # 截图原始物理像素 (desktop union 或单 app 窗口)
    logical_px: PxSize            # 对应的 desktop union 逻辑像素 (mouse / overlay 坐标用这套)
    scaled_px: PxSize             # 实际编码进 base64 的像素
    displays: list[DisplayInfo]   # 多屏时按主屏在前 副屏在后排; 单 app 截图也带, 方便定位
    note: str

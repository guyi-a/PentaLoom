"""computer-use 业务实现层. 跑 macOS AX / NSWorkspace / CGEvent API.

公共函数都先调 require_macos(), 平台错就抛 RuntimeError. PyObjC 模块**延迟 import**
在函数体内 — 非 macOS 装 PentaLoom 时这个文件本身能 import (因为 _platform 是纯 Python),
不会因为缺 ApplicationServices 模块直接挂.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

from pentaloom.capabilities.computer._models import (
    ActionResult,
    AppInfo,
    AXElement,
    PermissionStatus,
    SnapshotResult,
)
from pentaloom.capabilities.computer._platform import require_macos

# ── 常量 ──────────────────────────────────────────────────────

# 可交互元素的 role 集合 — 给 snapshot 统计 + LLM 优先关注用.
INTERACTIVE_ROLES = frozenset({
    "AXButton", "AXMenuButton", "AXPopUpButton",
    "AXTextField", "AXTextArea", "AXSearchField", "AXSecureTextField",
    "AXCheckBox", "AXRadioButton", "AXSlider", "AXStepper",
    "AXMenuItem", "AXMenuBarItem", "AXLink", "AXTabGroup",
    "AXComboBox", "AXIncrementor",
})

# 默认 snapshot 深度 / 每层最多 children — Phase 0 实测 100-300 节点足够 cover 常见 app.
DEFAULT_SNAPSHOT_DEPTH = 5
DEFAULT_MAX_CHILDREN = 30


# ── permission ────────────────────────────────────────────────


def check_permission(*, prompt: bool = False) -> PermissionStatus:
    """检查 AX 权限. prompt=True 时触发系统授权弹窗 (首次必须这样)."""
    require_macos()
    from ApplicationServices import (
        AXIsProcessTrusted,
        AXIsProcessTrustedWithOptions,
    )

    binary = sys.executable
    host_binary = os.path.realpath(binary)
    trusted = bool(AXIsProcessTrusted())
    prompt_triggered = False

    if not trusted and prompt:
        # 触发系统弹窗 "X 想要使用辅助功能控制电脑". X 是启动 python 的 GUI 宿主
        # (Terminal / iTerm / Electron), 不是 python 自己. 这是 macOS TCC 模型.
        AXIsProcessTrustedWithOptions({"AXTrustedCheckOptionPrompt": True})
        prompt_triggered = True

    if trusted:
        msg = "已授权, 可以正常调用 AX API"
    else:
        msg = (
            "未授权. macOS 不允许在 GUI '+' 手选裸 binary; 必须传 prompt=True 触发系统弹窗, "
            "点'打开系统设置'后, 在 Accessibility 列表里找到启动 python 的宿主进程 "
            "(Terminal / iTerm / 你的 Electron app) 开开关即可. 一旦宿主拿到权限, "
            "所有从它启的 python 子进程都自动有权限, 跟 venv / pyenv 路径无关."
        )
        if prompt_triggered:
            msg += " 这次调用已触发弹窗, 请到系统设置开开关后重试."

    return PermissionStatus(
        trusted=trusted,
        host_process=os.path.basename(host_binary),
        host_binary=host_binary,
        prompt_triggered=prompt_triggered,
        message=msg,
    )


# ── apps ──────────────────────────────────────────────────────


def list_apps() -> list[AppInfo]:
    """列正在跑的 app. 不需要 AX 权限.

    过滤策略: 跳过 Prohibited (policy=2, 纯后台 daemon / 系统服务) + 跳过没
    localizedName 的; 保留 Regular (0, 有 Dock icon) 和 Accessory (1, 菜单栏 app
    / 临时切到后台的 GUI app).

    为什么不只留 Regular: Notes / Mail / Music 等 Apple 原生 app 在某些状态会
    被报成 Accessory (后台启动 / 没活跃窗口时), 严过滤会让 LLM 看不到它们误以为
    没装. 保留 Accessory 后果是会多出 menubar 小工具 (Karabiner / Bartender 等),
    LLM 自己会按名字筛选, 不影响.
    """
    require_macos()
    from AppKit import NSWorkspace

    ws = NSWorkspace.sharedWorkspace()
    out: list[AppInfo] = []
    for a in ws.runningApplications():
        policy = a.activationPolicy()
        if policy == 2:  # NSApplicationActivationPolicyProhibited — 真后台
            continue
        name = str(a.localizedName() or "")
        if not name:
            continue
        out.append(AppInfo(
            pid=int(a.processIdentifier()),
            name=name,
            bundle=str(a.bundleIdentifier() or ""),
            active=bool(a.isActive()),
        ))
    out.sort(key=lambda x: (not x.active, x.name.lower()))
    return out


def _resolve_app_pid(target: str) -> int | None:
    """target 是数字 → pid; 字符串走四级匹配 (从严到松), 第一个命中返回 pid.

    1. 精确 localizedName 匹配 (大小写不敏感)
    2. 精确 bundleId 匹配
    3. substring 匹配 localizedName ("VSCode" 命中 "Visual Studio Code", "code" 命中 "Code")
    4. bundleId 末段匹配 ("lark" 命中 com.bytedance.lark)

    跨语言名 (用户说"飞书" 但 app localizedName 是 "Feishu") substring 解决不了,
    走 list_apps_candidates 给 LLM 拿候选列表自己挑.
    """
    if target.isdigit():
        return int(target)
    from AppKit import NSWorkspace

    ws = NSWorkspace.sharedWorkspace()
    target_lower = target.lower()
    candidates = []
    for a in ws.runningApplications():
        if a.activationPolicy() == 2:
            continue
        name = str(a.localizedName() or "")
        if name:
            candidates.append((a, name, str(a.bundleIdentifier() or "")))

    # 1) 精确 localizedName
    for a, name, _bundle in candidates:
        if name.lower() == target_lower:
            return int(a.processIdentifier())
    # 2) 精确 bundleId
    for a, _name, bundle in candidates:
        if bundle.lower() == target_lower:
            return int(a.processIdentifier())
    # 3) substring localizedName — 加长度保护防止 "app" / "代码" 这种泛词乱碰.
    #    至少 3 个字符才尝试 substring, 且只接受 target ⊂ name (单向),
    #    name ⊂ target 容易把短 app 名硬套到长输入里 (如 "App" ⊂ "不存在的 App").
    if len(target_lower) >= 3:
        for a, name, _bundle in candidates:
            lname = name.lower()
            if len(lname) >= 3 and target_lower in lname:
                return int(a.processIdentifier())
    # 4) bundleId 末段 — 必须**精确** match (target_lower == last), 不做 substring.
    #    bundle 末段"app" / "client" / "helper" 这种太普遍, substring 会乱命中.
    for a, _name, bundle in candidates:
        if not bundle:
            continue
        last = bundle.rsplit(".", 1)[-1].lower()
        if last and last == target_lower:
            return int(a.processIdentifier())
    return None


def list_app_candidates(target: str, *, limit: int = 20) -> list[AppInfo]:
    """target 找不到精确 pid 时, 给 LLM 一份候选 — 包含所有正在跑的 app, 按
    "包含 target 子串" 优先排, 让 LLM 一眼看清候选自己挑.

    覆盖 "飞书 vs Feishu" 这种跨语言无法机械匹配的场景.
    """
    apps = list_apps()
    target_lower = target.lower()

    def score(a: AppInfo) -> tuple[int, str]:
        name = a.name.lower()
        bundle = a.bundle.lower()
        # 越像越优先 (越小越靠前)
        if target_lower == name:
            return (0, name)
        if target_lower in name or name in target_lower:
            return (1, name)
        if target_lower in bundle or bundle.endswith(target_lower):
            return (2, name)
        return (3, name)

    apps.sort(key=score)
    return apps[:limit]


def _app_name(pid: int) -> str:
    from AppKit import NSWorkspace
    ws = NSWorkspace.sharedWorkspace()
    for a in ws.runningApplications():
        if int(a.processIdentifier()) == pid:
            return str(a.localizedName() or "")
    return ""


# ── snapshot (AX 树 dump + 扁平化) ────────────────────────────


def _get_attr(elem, attr) -> Any:
    """安全读 AX 属性, 失败 / 没设返 None."""
    from ApplicationServices import AXUIElementCopyAttributeValue
    try:
        err, value = AXUIElementCopyAttributeValue(elem, attr, None)
    except Exception:
        return None
    return value if err == 0 else None


def _short(s: Any, n: int = 100) -> str | None:
    if s is None:
        return None
    text = str(s)
    if len(text) <= n:
        return text
    return text[:n] + f"…(+{len(text) - n})"


def _walk(
    elem,
    *,
    depth: int,
    max_depth: int,
    max_children: int,
    out: list[tuple[Any, AXElement]],
) -> None:
    """深度优先扁平化. 副作用: 往 out append (raw_elem, AXElement) tuple.

    带 raw_elem 是为了后续 press / set_value 通过 index 反查到真正的 AXUIElement.
    """
    from ApplicationServices import (
        kAXChildrenAttribute,
        kAXDescriptionAttribute,
        kAXEnabledAttribute,
        kAXFocusedAttribute,
        kAXHelpAttribute,
        kAXRoleAttribute,
        kAXRoleDescriptionAttribute,
        kAXSubroleAttribute,
        kAXTitleAttribute,
        kAXValueAttribute,
    )

    role = _get_attr(elem, kAXRoleAttribute)
    if not role:
        return  # 无 role 的元素直接跳过 (通常是损坏的 AX 节点)

    idx = len(out)
    summary = AXElement(
        index=idx,
        role=str(role),
        subrole=_short(_get_attr(elem, kAXSubroleAttribute), 40),
        role_description=_short(_get_attr(elem, kAXRoleDescriptionAttribute), 40),
        title=_short(_get_attr(elem, kAXTitleAttribute), 100),
        value=_short(_get_attr(elem, kAXValueAttribute), 100),
        description=_short(_get_attr(elem, kAXDescriptionAttribute), 100),
        help=_short(_get_attr(elem, kAXHelpAttribute), 100),
        enabled=_get_attr(elem, kAXEnabledAttribute),
        focused=_get_attr(elem, kAXFocusedAttribute) or None,
        depth=depth,
    )
    out.append((elem, summary))

    if depth >= max_depth:
        return
    children = _get_attr(elem, kAXChildrenAttribute) or []
    for c in children[:max_children]:
        _walk(
            c,
            depth=depth + 1,
            max_depth=max_depth,
            max_children=max_children,
            out=out,
        )


def snapshot(
    target: str,
    *,
    depth: int = DEFAULT_SNAPSHOT_DEPTH,
    max_children: int = DEFAULT_MAX_CHILDREN,
) -> tuple[SnapshotResult, list]:
    """拍一次 AX snapshot. 返回 (model, raw_elements) — raw_elements 跟 model.elements 同序,
    后续按 index 反查做 press / set_value 用.

    一次性内存活在调用方, 不缓存 — 后续操作前应再 snapshot 拿新 index (跟 browser_bridge
    的 read_state 同款瞬态语义).
    """
    require_macos()
    from ApplicationServices import AXUIElementCreateApplication

    pid = _resolve_app_pid(target)
    if pid is None:
        raise ValueError(f"找不到 app: {target!r}")

    elem = AXUIElementCreateApplication(pid)
    out: list[tuple[Any, AXElement]] = []
    t0 = time.time()
    _walk(elem, depth=0, max_depth=depth, max_children=max_children, out=out)
    elapsed_ms = int((time.time() - t0) * 1000)

    elements = [e for _, e in out]
    raw_elements = [r for r, _ in out]
    interactive = sum(1 for e in elements if e.role in INTERACTIVE_ROLES)

    result = SnapshotResult(
        app_name=_app_name(pid),
        pid=pid,
        elapsed_ms=elapsed_ms,
        total_elements=len(elements),
        interactive_elements=interactive,
        elements=elements,
    )
    return result, raw_elements


# ── press / set_value / focus ─────────────────────────────────


def _perform_action(elem, action_name: str) -> bool:
    """调 AXUIElementPerformAction. 返 True 表示成功."""
    from ApplicationServices import AXUIElementPerformAction
    try:
        err = AXUIElementPerformAction(elem, action_name)
    except Exception:
        return False
    return err == 0


def press(raw_elem, element_desc: str) -> ActionResult:
    """对元素执行 AXPress. 适合 button / menu item / link / checkbox 等."""
    require_macos()
    ok = _perform_action(raw_elem, "AXPress")
    return ActionResult(
        action="press",
        target_description=element_desc,
        success=ok,
        message="AXPress 成功" if ok else "AXPress 失败 (元素不支持或已失效)",
    )


def set_value(raw_elem, value: str, element_desc: str) -> ActionResult:
    """给文本框 / slider 设值. 适合 AXTextField / AXTextArea / AXSlider 等."""
    require_macos()
    from ApplicationServices import (
        AXUIElementSetAttributeValue,
        kAXValueAttribute,
    )
    try:
        err = AXUIElementSetAttributeValue(raw_elem, kAXValueAttribute, value)
        ok = err == 0
    except Exception as e:  # noqa: BLE001
        return ActionResult(
            action="set_value",
            target_description=element_desc,
            success=False,
            message=f"AXSetValue 异常: {e}",
        )
    return ActionResult(
        action="set_value",
        target_description=element_desc,
        success=ok,
        message=f"AXSetValue {'成功' if ok else '失败'} (err={err})",
    )


def focus_app(target: str) -> ActionResult:
    """把指定 app 切到前台 (相当于 Dock 点图标)."""
    require_macos()
    from AppKit import NSWorkspace

    pid = _resolve_app_pid(target)
    if pid is None:
        return ActionResult(
            action="focus",
            target_description=target,
            success=False,
            message=f"找不到 app: {target!r}",
        )
    ws = NSWorkspace.sharedWorkspace()
    for a in ws.runningApplications():
        if int(a.processIdentifier()) == pid:
            # activateWithOptions 在 macOS 14+ 上会忽略 NSApplicationActivateIgnoringOtherApps,
            # 但 activate() 还有效.
            try:
                a.activate()
            except Exception:
                a.activateWithOptions_(0)
            return ActionResult(
                action="focus",
                target_description=f"{a.localizedName()} (pid={pid})",
                success=True,
                message="已切到前台",
            )
    return ActionResult(
        action="focus",
        target_description=target,
        success=False,
        message="pid 找到了但 NSRunningApplication 拿不到",
    )


# ── menu (跨 app 普适, Phase 0 实测最稳的入口) ─────────────────


def menu_action(app: str, path: list[str]) -> ActionResult:
    """按菜单路径找到 menu item 并 AXPress.

    path 是从 menubar item 名开始的: ["文件", "新建文件夹"]
    支持中文 / 英文 — AX title 是本地化的.
    """
    require_macos()
    from ApplicationServices import (
        AXUIElementCopyAttributeValue,
        AXUIElementCreateApplication,
        kAXChildrenAttribute,
        kAXMenuBarAttribute,
        kAXTitleAttribute,
    )

    if not path:
        return ActionResult(
            action="menu", target_description=f"{app} > (空 path)",
            success=False, message="path 不能为空",
        )

    pid = _resolve_app_pid(app)
    if pid is None:
        return ActionResult(
            action="menu", target_description=f"{app} > {' > '.join(path)}",
            success=False, message=f"找不到 app: {app!r}",
        )

    app_elem = AXUIElementCreateApplication(pid)
    menubar = _get_attr(app_elem, kAXMenuBarAttribute)
    if menubar is None:
        return ActionResult(
            action="menu", target_description=f"{app} > {' > '.join(path)}",
            success=False, message="app 没有 menubar (Electron 子窗口 / 后台 app 常见)",
        )

    current = menubar
    for i, segment in enumerate(path):
        children = _get_attr(current, kAXChildrenAttribute) or []
        match = None
        for child in children:
            title = _get_attr(child, kAXTitleAttribute)
            if title and str(title) == segment:
                match = child
                break
        if match is None:
            available = [
                str(_get_attr(c, kAXTitleAttribute) or "")
                for c in children
                if _get_attr(c, kAXTitleAttribute)
            ]
            return ActionResult(
                action="menu",
                target_description=f"{app} > {' > '.join(path[:i+1])}",
                success=False,
                message=(
                    f"找不到 menu item {segment!r}. 当前层可选: "
                    f"{available[:20]}{'…' if len(available) > 20 else ''}"
                ),
            )
        # menubar item / menu item 都有 children = AXMenu, 真正的菜单项在 AXMenu 里
        # 不是最后一段时, 下钻一层 (穿过 AXMenu)
        if i < len(path) - 1:
            sub_children = _get_attr(match, kAXChildrenAttribute) or []
            # 如果 match 自己就有 menu item 类 children, 直接用; 否则下钻到 AXMenu
            if sub_children and any(
                str(_get_attr(c, kAXTitleAttribute) or "") == path[i + 1]
                for c in sub_children
            ):
                current = match
            else:
                # 下钻到 AXMenu 这一层
                if sub_children:
                    current = sub_children[0]  # 通常 menubar item / menu item 第一个 child 是 AXMenu
                else:
                    current = match
        else:
            # 最后一段, 直接 press
            ok = _perform_action(match, "AXPress")
            return ActionResult(
                action="menu",
                target_description=f"{app} > {' > '.join(path)}",
                success=ok,
                message="菜单项已点击" if ok else "AXPress 失败 (item 可能 disabled)",
            )

    return ActionResult(
        action="menu", target_description=f"{app} > {' > '.join(path)}",
        success=False, message="逻辑不应到这里",
    )


# ── key (走 CGEvent, 不靠 AX, 兜底通用) ────────────────────────


# 常用键名 → CGKeyCode (macOS HIToolbox 定义). 不全, 够用.
_KEY_CODE_MAP = {
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05,
    "z": 0x06, "x": 0x07, "c": 0x08, "v": 0x09, "b": 0x0B, "q": 0x0C,
    "w": 0x0D, "e": 0x0E, "r": 0x0F, "y": 0x10, "t": 0x11,
    "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "5": 0x17, "6": 0x16,
    "7": 0x1A, "8": 0x1C, "9": 0x19, "0": 0x1D,
    "p": 0x23, "o": 0x1F, "i": 0x22, "u": 0x20, "n": 0x2D, "m": 0x2E,
    "j": 0x26, "k": 0x28, "l": 0x25,
    "return": 0x24, "enter": 0x24,
    "tab": 0x30, "space": 0x31,
    "delete": 0x33, "backspace": 0x33,
    "escape": 0x35, "esc": 0x35,
    "left": 0x7B, "right": 0x7C, "down": 0x7D, "up": 0x7E,
    "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76, "f5": 0x60, "f6": 0x61,
    "f7": 0x62, "f8": 0x64, "f9": 0x65, "f10": 0x6D, "f11": 0x67, "f12": 0x6F,
}

# 修饰键 → kCGEventFlagMaskXxx (CGEventFlags bitmask).
# 跟系统约定: "cmd+l" 表示按住 Cmd 同时按 L.
_MODIFIER_FLAGS = {
    "cmd":   1 << 20,    # kCGEventFlagMaskCommand
    "shift": 1 << 17,    # kCGEventFlagMaskShift
    "alt":   1 << 19,    # kCGEventFlagMaskAlternate
    "option": 1 << 19,
    "ctrl":  1 << 18,    # kCGEventFlagMaskControl
    "control": 1 << 18,
    "fn":    1 << 23,    # kCGEventFlagMaskSecondaryFn
}


def send_key(combo: str) -> ActionResult:
    """发一个键盘组合, e.g. 'cmd+s' / 'cmd+shift+t' / 'escape' / 'return'.

    + 分隔. 最后一段必须是 _KEY_CODE_MAP 里的实键, 前面是 modifier.
    不区分大小写. modifier 顺序不限.
    """
    require_macos()
    from Quartz import (
        CGEventCreateKeyboardEvent,
        CGEventPost,
        CGEventSetFlags,
        kCGHIDEventTap,
    )

    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        return ActionResult(
            action="key", target_description=combo,
            success=False, message="key combo 为空",
        )

    *mods, key = parts
    if key not in _KEY_CODE_MAP:
        return ActionResult(
            action="key", target_description=combo,
            success=False,
            message=f"未识别的键名 {key!r}. 已支持: {sorted(_KEY_CODE_MAP.keys())[:20]}…",
        )
    flags = 0
    for m in mods:
        if m not in _MODIFIER_FLAGS:
            return ActionResult(
                action="key", target_description=combo,
                success=False,
                message=f"未识别的修饰键 {m!r}. 已支持: {sorted(_MODIFIER_FLAGS.keys())}",
            )
        flags |= _MODIFIER_FLAGS[m]

    keycode = _KEY_CODE_MAP[key]
    # 按下 + 抬起 两个事件, 中间留一点点延迟让 app 能区分
    down = CGEventCreateKeyboardEvent(None, keycode, True)
    up = CGEventCreateKeyboardEvent(None, keycode, False)
    if flags:
        CGEventSetFlags(down, flags)
        CGEventSetFlags(up, flags)
    CGEventPost(kCGHIDEventTap, down)
    time.sleep(0.02)
    CGEventPost(kCGHIDEventTap, up)
    return ActionResult(
        action="key", target_description=combo,
        success=True, message="键盘事件已发送",
    )

"""paste: NSPasteboard + Cmd+V, 操作前后备份/恢复用户剪贴板.

为什么不直接 type unicode: 中文 IME 激活时 CGEventKeyboardSetUnicodeString 的键码
会被吃掉; 速度也慢 (一字符一事件). pasteboard 走系统级 paste 路径, CJK + emoji
完美 (phase 0 实测).

副作用是会污染用户剪贴板, 操作前 save 一段, 完成后 restore. sleep 用 asyncio.sleep
(协程友好); time 不阻塞 event loop.

复用 service.send_key('cmd+v') 不重复实现 Cmd+V keycode.
"""

from __future__ import annotations

import asyncio

from pentaloom.capabilities.computer._models import ActionResult
from pentaloom.capabilities.computer._platform import require_macos


async def paste_text(text: str) -> ActionResult:
    """把 text 写剪贴板 → 发 Cmd+V → 恢复用户原剪贴板.

    text: 任意 unicode (含 CJK / emoji)
    返 success=True 即流程跑完, 不保证目标 app 真接收 (焦点不在输入框就吞)
    """
    require_macos()
    from AppKit import NSPasteboard, NSPasteboardTypeString

    from pentaloom.capabilities.computer.service import send_key

    pb = NSPasteboard.generalPasteboard()
    saved = pb.stringForType_(NSPasteboardTypeString)  # 可能 None (剪贴板为空 / 非字符串内容)

    try:
        pb.clearContents()
        pb.setString_forType_(text, NSPasteboardTypeString)
        # 让 pasteboard 落地 — macOS 写后立刻读偶尔会拿到旧值, 50ms 够
        await asyncio.sleep(0.05)

        key_result = send_key("cmd+v")
        if not key_result.success:
            return ActionResult(
                action="paste",
                target_description=f"len={len(text)}",
                success=False,
                message=f"Cmd+V 发送失败: {key_result.message}",
            )

        # 等系统粘贴行为完成 — 不同 app 响应速度不同 (浏览器 ~50ms, IntelliJ 可能 300ms+)
        # 取保守的 150ms; 慢 app 用户感知会"粘了但剪贴板还没恢复" — 极少见, 接受.
        await asyncio.sleep(0.15)
    finally:
        # 始终恢复用户剪贴板, 哪怕 Cmd+V 失败.
        # saved 是 None 说明用户原本剪贴板就空, 我们就 clear 即可 (现在还在 text).
        pb.clearContents()
        if saved is not None:
            pb.setString_forType_(saved, NSPasteboardTypeString)

    return ActionResult(
        action="paste",
        target_description=f"len={len(text)}",
        success=True,
        message=f"已粘贴 {len(text)} 字符 (剪贴板已恢复用户原内容)",
    )

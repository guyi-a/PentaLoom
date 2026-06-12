"""server-injected prompt block 的 strip helper. 单一真相源.

为什么需要:
  附件发送时, 后端把 <pentaloom_internal_attachments> 块 prepend 到用户文本前
  喂给 SDK; SDK 把整 prompt 写进 messages.jsonl + SQLiteSessionStore 镜像.
  历史回放路径 (sessions.py /messages) 必须在出口处剥掉这块, 否则用户翻历史
  会看到 internal block (meta 信息泄漏 + 视觉杂讯).

  live + resume 路径不需要 strip — chat.py 那边 set_user_prompt 时存的就是
  display 版本 (用户纯文本), internal_prompt 只走 pl.query, 跟 buf.user_prompt
  解耦. 见 docs/attachment-upload-plan.md §5.3.

设计:
  - 只识别"块在 prompt 开头" 的情况 (anchored regex). 防御性 + 跟 chat.py 拼
    block 的方式严格对应 (内部块永远 prepend, 不会 mid-prompt).
  - tag 用 <pentaloom_internal_attachments> 私有命名空间, 用户即使粘 XML 也
    撞不上. 启动锚点是第二层防御, 不是唯一防御.
  - 返 (stripped_text, attachment_count) — count 给前端渲染 "📎 N 个文件"
    占位 (text 空 + count > 0 时), 跟 live 路径的 user_prompt frame 字段同源.
  - 函数名复数 — 给未来其它 internal block (weaver propose / system notice)
    留扩展位, 加 tag 时只扩 regex 不动调用方.
"""

from __future__ import annotations

import re

# 锚点匹配 prompt 开头的 <pentaloom_internal_attachments...>...</pentaloom_internal_attachments>
# 块. DOTALL 让 . 跨行, 因为 block 主体是多行 markdown.
_ATTACHMENTS_RE = re.compile(
    r"^\s*<pentaloom_internal_attachments(?:\s[^>]*)?>(.*?)</pentaloom_internal_attachments>\s*\n+",
    re.DOTALL,
)

# 数 block 主体里 "- `attachments/...`" 这种 bullet, 反推附件个数.
# 跟 chat.py 拼 block 时的格式严格对应; chat.py 改格式时这里要一起改.
_BULLET_RE = re.compile(r"^\s*-\s+`attachments/", re.MULTILINE)


def strip_internal_prompt_blocks(text: str) -> tuple[str, int]:
    """从 prompt 开头剥掉 server-injected 内部块, 返 (stripped, attachment_count).

    没匹配到块时原样返 (text, 0). 用于 routers/sessions.py 历史出口.
    """
    m = _ATTACHMENTS_RE.match(text)
    if m is None:
        return text, 0
    body = m.group(1)
    count = len(_BULLET_RE.findall(body))
    return text[m.end():], count


def build_attachments_block(paths: list[str]) -> str:
    """拼一个 <pentaloom_internal_attachments> 块. 返带末尾 "\\n\\n" 的字符串,
    可直接 + 用户文本组成 internal_prompt.

    paths 是相对 sandbox 的路径 (e.g. "attachments/report.pdf").
    """
    bullets = "\n".join(f"- `{p}`" for p in paths)
    return (
        "<pentaloom_internal_attachments>\n"
        "The user attached these files to this message. They are already "
        "committed into the current workspace.\n\n"
        f"{bullets}\n\n"
        "Use `Read` for text/PDF/images and `file_read` for docx/pptx/xlsx "
        "when needed.\n"
        "</pentaloom_internal_attachments>\n\n"
    )

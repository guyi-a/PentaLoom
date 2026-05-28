"""运行环境段: 把系统装了什么 CJK 字体注进 prompt, 让模型选真存在的."""

from __future__ import annotations

# 跟 tools/system_resources.py 同名常量 — 这边不 import 防潜在循环
# (env 在 prompts/, 比 tools/ 早一步组装; tools/__init__.py 可能反过来需要 prompts).
_INSTALL_NOTO_SANS_SC_FULL_NAME = "mcp__pentaloom__install_noto_sans_sc"

# 清单太长浪费 prompt token; 8 个足够 LLM 选了 (优先级前 8 必覆盖 NotoSans + 平台 native).
_MAX_LIST = 8


def render_runtime_env(*, available_cjk_fonts: list[str]) -> str:
    if not available_cjk_fonts:
        return (
            "## 运行环境\n"
            "系统**未探测到任何 CJK 字体**. 中文 PPT / 中文报告会渲染成豆腐字 (□). "
            f"做中文内容前先调 {_INSTALL_NOTO_SANS_SC_FULL_NAME} 把 Noto Sans SC 装上 "
            "(用户会被请求授权), 再继续."
        )
    fonts = ", ".join(available_cjk_fonts[:_MAX_LIST])
    return (
        "## 运行环境\n"
        f"系统已装的 CJK 字体 (按推荐顺序): {fonts}.\n"
        "中文 PPT 写 east_asian_name 时优先选这个清单里的名字 — "
        "verify autofix 默认注第一项. "
        f"清单不够用 (例如要装 Noto Sans SC) 调 {_INSTALL_NOTO_SANS_SC_FULL_NAME}."
    )

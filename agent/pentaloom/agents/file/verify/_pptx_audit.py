"""PPTX 三项 audit: 字体 tofu / 几何越界 / 空 slide.

字体 tofu 思路抄 krow_pptx/audit/fonts: 中文文本 run 必须有 east_asian_name
指向 CJK 字体; 否则查看端可能用 latin 字体 fallback, 中文显示成"豆腐字" (□).

几何越界: shape 的 left/top/width/height 推算 right/bottom, 跟 slide 宽高
比较; 差 > 10pt (= 127000 EMU) 算 blocking. 留 10pt 是因为 PPT 里挂边出血
是常见设计手法, 严格 0 容差会把合法布局误报为越界.
"""

from __future__ import annotations

from pentaloom.agents.file._models import Issue

# 1 pt = 12700 EMU. 留 10pt 容差.
_GEO_TOLERANCE_EMU = 10 * 12700

# 含 CJK 统一汉字基本区 / 扩展 A / 兼容汉字 / 假名 / 朝鲜文 — 命中任一就需要 EA 字体
_CJK_RANGES = (
    (0x3000, 0x303F),    # CJK 符号
    (0x3040, 0x309F),    # 平假名
    (0x30A0, 0x30FF),    # 片假名
    (0x3400, 0x4DBF),    # CJK 扩展 A
    (0x4E00, 0x9FFF),    # CJK 基本区
    (0xF900, 0xFAFF),    # CJK 兼容汉字
    (0xFF00, 0xFFEF),    # 全角符号
    (0xAC00, 0xD7AF),    # 朝鲜文
)

# 已知支持 CJK 的常见字体名 — 命中即认为 EA font 是合理的 (不报 tofu).
# 不强求穷举, 主要是排掉 LLM 经常生成的 "Arial" / "Calibri" / "Helvetica" 等
# latin-only 字体. 后续可在 settings 加用户扩展.
_CJK_SAFE_FONTS = frozenset({
    "Noto Sans SC", "Noto Sans CJK SC", "Noto Sans CJK TC", "Noto Sans CJK JP",
    "Noto Sans CJK KR", "Noto Sans JP", "Noto Sans KR", "Noto Sans TC",
    "Noto Serif SC", "Noto Serif CJK SC", "Source Han Sans", "Source Han Sans SC",
    "Source Han Sans CN", "Source Han Sans TW", "Source Han Sans JP",
    "Source Han Serif", "Source Han Serif SC", "PingFang SC", "PingFang TC",
    "PingFang HK", "PingFang", "Microsoft YaHei", "Microsoft YaHei UI",
    "SimHei", "SimSun", "NSimSun", "FangSong", "KaiTi", "DengXian",
    "Hiragino Sans GB", "Hiragino Kaku Gothic Pro", "Yu Gothic", "Meiryo",
    "MS Gothic", "MS Mincho", "Malgun Gothic", "Apple SD Gothic Neo",
    "Heiti SC", "Heiti TC", "STHeiti", "STSong", "STKaiti", "STFangsong",
    "WenQuanYi Micro Hei", "WenQuanYi Zen Hei",
})


def _has_cjk(text: str) -> bool:
    for ch in text:
        cp = ord(ch)
        for lo, hi in _CJK_RANGES:
            if lo <= cp <= hi:
                return True
    return False


def _is_cjk_safe(font_name: str | None) -> bool:
    if not font_name:
        return False
    return font_name in _CJK_SAFE_FONTS


def audit_fonts(prs) -> list[Issue]:
    """遍历每个 slide 每个 run, 中文 run 缺 EA 字体或 EA 字体非 CJK → blocking."""
    issues: list[Issue] = []
    for s_idx, slide in enumerate(prs.slides, start=1):
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for p_idx, para in enumerate(shape.text_frame.paragraphs, start=1):
                for r_idx, run in enumerate(para.runs, start=1):
                    text = run.text or ""
                    if not _has_cjk(text):
                        continue
                    # python-pptx 的 run.font.name 读 latin; east_asian 字体得读底层 XML
                    name = run.font.name
                    ea_name = _read_ea_font(run)
                    # 优先看 EA; 若 EA 缺, 退而看 latin (有些模板把 CJK 字体设成 latin 也能渲对)
                    used = ea_name or name
                    if not _is_cjk_safe(used):
                        loc = f"Slide {s_idx} / Shape {shape.shape_id} / Para {p_idx} / Run {r_idx}"
                        if not ea_name:
                            msg = (
                                "中文 run 缺 east_asian_name; 当前 latin={!r}; "
                                "查看端可能 fallback 成 latin 字体显示豆腐字".format(name)
                            )
                        else:
                            msg = (
                                f"east_asian_name={ea_name!r} 不在已知 CJK 字体白名单, "
                                "可能在用户机上 fallback 失败"
                            )
                        issues.append(Issue(
                            tier="tier1",
                            kind="font_tofu",
                            severity="blocking",
                            location=loc,
                            message=msg,
                        ))
    return issues


def _read_ea_font(run) -> str | None:
    """读 run 底层 XML 的 a:rPr/a:ea/@typeface (east-asian 字体名).

    python-pptx 高层 API 只提供 latin (font.name), 没暴露 EA. 直接打 lxml 拿.
    """
    rPr = run._r.find(
        "{http://schemas.openxmlformats.org/drawingml/2006/main}rPr"
    )
    if rPr is None:
        return None
    ea = rPr.find("{http://schemas.openxmlformats.org/drawingml/2006/main}ea")
    if ea is None:
        return None
    return ea.get("typeface")


def audit_geometry(prs) -> list[Issue]:
    """shape 的 left/top + width/height vs slide 宽高 (EMU). 越界 > 10pt 算 blocking."""
    issues: list[Issue] = []
    sw = prs.slide_width
    sh = prs.slide_height
    if sw is None or sh is None:
        return issues
    for s_idx, slide in enumerate(prs.slides, start=1):
        for shape in slide.shapes:
            left = shape.left
            top = shape.top
            width = shape.width
            height = shape.height
            if None in (left, top, width, height):
                continue
            right = left + width
            bottom = top + height
            overruns: list[str] = []
            if left < -_GEO_TOLERANCE_EMU:
                overruns.append(f"left={left} < 0")
            if top < -_GEO_TOLERANCE_EMU:
                overruns.append(f"top={top} < 0")
            if right > sw + _GEO_TOLERANCE_EMU:
                overruns.append(f"right={right} > slide_width={sw}")
            if bottom > sh + _GEO_TOLERANCE_EMU:
                overruns.append(f"bottom={bottom} > slide_height={sh}")
            if overruns:
                issues.append(Issue(
                    tier="tier1",
                    kind="geometry_overflow",
                    severity="blocking",
                    location=f"Slide {s_idx} / Shape {shape.shape_id} ({shape.name})",
                    message="; ".join(overruns),
                ))
    return issues


def audit_content(prs) -> list[Issue]:
    """空 slide: shapes 全空 / 全是 placeholder 但 placeholder 没文本. warning 级.

    设计上故意空白页 (过渡 slide) 也会命中, 所以不 block, 只提示.
    """
    issues: list[Issue] = []
    for s_idx, slide in enumerate(prs.slides, start=1):
        has_content = False
        for shape in slide.shapes:
            if shape.has_text_frame and (shape.text_frame.text or "").strip():
                has_content = True
                break
            if getattr(shape, "has_table", False):
                has_content = True
                break
            if shape.shape_type and not shape.is_placeholder:
                # 非 placeholder 的形状 (图片 / 自选图) 认为有内容
                has_content = True
                break
        if not has_content:
            issues.append(Issue(
                tier="tier1",
                kind="empty_slide",
                severity="warning",
                location=f"Slide {s_idx}",
                message="slide 没有可见文本/表格/图片 (若为过渡页可忽略)",
            ))
    return issues

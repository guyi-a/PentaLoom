"""PPTX auto-fix: 给中文 run 注入 east_asian 字体引用.

默认从 detect_cjk_fonts() 取第一项 (系统真装的字体), 探不到回退 DEFAULT_CJK_FONT
(Noto Sans SC) — 至少在装了 Noto 的机器上能渲对.

不真嵌入字体 — 仅修改 a:rPr/a:ea/@typeface. 查看端没装该字体仍 fallback,
M5+ 再补真嵌入 ([Content_Types].xml + ppt/fonts/* + presentation.xml 的 embeddedFontLst).
"""

from __future__ import annotations

from pentaloom.capabilities.file.verify._pptx_audit import _has_cjk

_DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS = f"{{{_DRAWING_NS}}}"

DEFAULT_CJK_FONT = "Noto Sans SC"


def fix_fonts(prs, *, cjk_font: str | None = None) -> tuple[int, str]:
    """给所有中文 run 注入 east_asian_name. 返回 (修改的 run 数, 实际注入的字体名).

    cjk_font=None: 从 detect_cjk_fonts() 取第一项, 系统什么都没装时回退
    DEFAULT_CJK_FONT — 这种情况下注的字体在用户机上仍可能 fallback,
    所以 verify 报告会强提示"清单为空, 该装字体了".
    """
    from lxml import etree  # type: ignore[import-not-found]

    if cjk_font is None:
        from pentaloom.infra.fonts import detect_cjk_fonts

        installed = detect_cjk_fonts()
        cjk_font = installed[0] if installed else DEFAULT_CJK_FONT

    fixed = 0
    for slide in prs.slides:
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for para in shape.text_frame.paragraphs:
                for run in para.runs:
                    if not _has_cjk(run.text or ""):
                        continue
                    if _ensure_ea(run, cjk_font, etree):
                        fixed += 1
    return fixed, cjk_font


def _ensure_ea(run, cjk_font: str, etree) -> bool:
    """确保 run 有 a:rPr/a:ea 且 typeface=cjk_font. 改了返 True, 已对返 False."""
    r = run._r
    rPr = r.find(f"{_NS}rPr")
    if rPr is None:
        # 没有 rPr 的 run 几乎不可能 (python-pptx 默认会建), 但兜底建一个并塞到 r 最前
        rPr = etree.SubElement(r, f"{_NS}rPr")
        # python-pptx 期望 rPr 是 r 的第一个子元素
        r.insert(0, rPr)

    ea = rPr.find(f"{_NS}ea")
    if ea is None:
        ea = etree.SubElement(rPr, f"{_NS}ea")
        ea.set("typeface", cjk_font)
        return True

    current = ea.get("typeface")
    if current == cjk_font:
        return False
    ea.set("typeface", cjk_font)
    return True

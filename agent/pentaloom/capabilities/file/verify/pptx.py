"""PPTX 质量门入口: 跑三检 + 可选 fix_fonts, 保存覆盖原文件."""

from __future__ import annotations

from pathlib import Path

from pentaloom.capabilities.file._models import Issue, VerifyReport
from pentaloom.capabilities.file.verify._pptx_audit import (
    audit_content,
    audit_fonts,
    audit_geometry,
)
from pentaloom.capabilities.file.verify._pptx_fix import fix_fonts


def verify_pptx(path: Path, *, autofix: bool) -> VerifyReport:
    from pptx import Presentation  # type: ignore[import-not-found]

    prs = Presentation(str(path))
    issues: list[Issue] = []
    fixes_applied: list[str] = []

    font_issues = audit_fonts(prs)
    geo_issues = audit_geometry(prs)
    content_issues = audit_content(prs)

    if autofix and font_issues:
        # 改 EA 字体不动几何, 不影响后续 audit_geometry 结果
        n, used_font = fix_fonts(prs)
        if n > 0:
            fixes_applied.append(f"fonts:{used_font}")
            # 修完字体: 之前的 font_tofu issue 转 warning 标"已修"
            font_issues = [
                Issue(
                    tier=it.tier,
                    kind=it.kind,
                    severity="warning",
                    location=it.location,
                    message=f"{it.message} (已注入 {used_font})",
                )
                for it in font_issues
            ]
            prs.save(str(path))

    # geometry 不 autofix (clamp 容易破坏布局) — 直接报 issue 让 agent 看
    issues.extend(font_issues)
    issues.extend(geo_issues)
    issues.extend(content_issues)

    blocking = sum(1 for it in issues if it.severity == "blocking")
    warning = sum(1 for it in issues if it.severity == "warning")
    return VerifyReport(
        path=str(path),
        kind="pptx",
        ok=blocking == 0,
        blocking_count=blocking,
        warning_count=warning,
        fixes_applied=fixes_applied,
        issues=issues,
    )

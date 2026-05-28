"""PDF 质量门: 空页检测 + auto-fix 删空页. 走 pymupdf (import 名 fitz).

空页定义 = page.get_text() 全空 + page.get_images() 为空. 这两个都查
最直接 — drawing / annotation 不算"内容"是务实选择, PPT 转 PDF 的封面页
往往只有矢量背景, 不该当成空页删.
"""

from __future__ import annotations

from pathlib import Path

from pentaloom.capabilities.file._models import Issue, VerifyReport


def _is_blank_page(page) -> bool:
    if (page.get_text() or "").strip():
        return False
    if page.get_images():
        return False
    # 矢量绘图 (drawings) 也算内容 — 防误删带 logo / 几何图案的封面
    if page.get_drawings():
        return False
    return True


def verify_pdf(path: Path, *, autofix: bool) -> VerifyReport:
    import fitz  # type: ignore[import-not-found]  # pymupdf

    issues: list[Issue] = []
    fixes_applied: list[str] = []

    doc = fitz.open(str(path))
    try:
        # 收集空页 — 1-indexed 给 LLM 看 (跟 PDF reader 的页码一致)
        blank_indices_0: list[int] = []
        for i in range(doc.page_count):
            if _is_blank_page(doc.load_page(i)):
                blank_indices_0.append(i)

        for i in blank_indices_0:
            issues.append(Issue(
                tier="tier1",
                kind="empty_page",
                severity="blocking",
                location=f"page {i + 1}",
                message="该页无文本/图片/矢量内容",
            ))

        if autofix and blank_indices_0:
            # 倒序删, 否则 index 错位
            for i in sorted(blank_indices_0, reverse=True):
                doc.delete_page(i)
            # pymupdf 不允许 doc.save 覆盖打开的同名文件 (非 incremental);
            # incremental 又不支持删页这种结构改动. 走 tmp + atomic rename.
            tmp = path.with_suffix(path.suffix + ".pl_tmp")
            doc.save(str(tmp), deflate=True)
            doc.close()
            tmp.replace(path)
            # 把 doc 指向修复后的文件, 让 finally 的 close() 仍然安全
            doc = fitz.open(str(path))
            fixes_applied.append("empty_pages")
            # 修完空页就不应再 blocking — issues 转为 warning 标"已修"
            issues = [
                Issue(
                    tier=it.tier,
                    kind=it.kind,
                    severity="warning",
                    location=it.location,
                    message=f"{it.message} (已删除)",
                )
                if it.kind == "empty_page"
                else it
                for it in issues
            ]
    finally:
        doc.close()

    blocking = sum(1 for it in issues if it.severity == "blocking")
    warning = sum(1 for it in issues if it.severity == "warning")
    return VerifyReport(
        path=str(path),
        kind="pdf",
        ok=blocking == 0,
        blocking_count=blocking,
        warning_count=warning,
        fixes_applied=fixes_applied,
        issues=issues,
    )

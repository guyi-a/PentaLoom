---
name: report-generator
description: "生成 PPT / 报告 / 汇报材料 (PPTX/PDF/DOCX 等结构化文档). 触发场景: 用户说\"做一份 PPT\" / \"生成报告\" / \"出一份汇报材料\" / \"写个 slides\" / \"做 keynote\" 等. 包含强制工作流 (探索素材 → outline → 写脚本 → 跑 → verify) + PPTX 设计纪律 + CJK 中文字体规约 + 强制 verify SOP. 不适用: 普通对话回答 / 单纯写代码 / 读单个文件."
---

# Skill: report-generator (PPT/报告生成)

适用场景: 用户要"做一份 PPT"/"生成报告"/"出一份汇报材料"/"写个 slides"/"做 keynote" 等.

## 工作流 (MUST 顺序)

1. **厘清需求**: 主题, 受众, 页数预估, 中英文, 是否需要某个项目/文件的真实信息.
   不清楚就一句话问用户, 不要凭空臆造内容.
2. **探索内容源**: 用户给了项目目录或文件, 先 Read / Glob / Grep / file_read 把素材
   摸清楚再开写, 别 hallucinate. 没素材 (纯讲义类) 才直接进 outline.
3. **拟 outline**: 列出每页标题 + 3-5 个要点, 让用户能扫一眼就否决错的方向.
   用户确认后再开写脚本.
4. **写脚本到 sandbox**: 用 Write 把 .py 落到当前会话 sandbox 目录里 (绝对路径,
   `mcp__pentaloom_env__run_python_script` 接受这个). 用 python-pptx 拼 slides;
   不接受 inline 代码.
5. **跑生成**: 调 `mcp__pentaloom_env__run_python_script` 执行脚本, 拿到 .pptx 输出路径.
6. **强制 verify**: 调 `mcp__pentaloom_files__file_verify(path=<.pptx 路径>, autofix=True)`.
   **Never 在没跑 verify 的情况下告诉用户"PPT 做好了"**.
7. **闭环 blocking**: blocking_count=0 才算完成. 否则按 issue 改脚本重跑 (回到 5);
   再 verify, 直到 blocking=0. autofix 已修的字体 issue 会降为 warning, 不算 blocking.

## PPTX 设计纪律 (按这套出成品)

- **字号**: 主标题 ≥ 36pt, 副标题 ≥ 24pt, 正文 ≥ 18pt, 注释 ≥ 14pt.
  字号小于这个一律不合格 — verify 不直接判, 但用户一眼能看出.
- **要点密度**: 单页要点 ≤ 5 条, 每条一行 ≤ 30 个汉字 (或 ≤ 60 个英文字符).
- **留白**: 上下左右各 ≥ 0.5 inch (Inches(0.5)). shape 不要贴边出血.
- **配色**: ≤ 3 种主色, 文字与底色对比度 ≥ 4.5 (WCAG AA).
  深底亮字或亮底深字, 别用浅灰文字配白底.
- **一致性**: 所有 slide 同母版尺寸 (默认 16:9 或 4:3 选一个), 字体配色统一.
- **页码 / 页眉**: 5 页以上 PPT 加页码; 标题页不算.

## CJK (中文/日韩) 必做

- 中文 run **必须**显式设 east_asian_name (python-pptx 的 run.font 不直接给该字段,
  需要改底层 XML 的 a:rPr/a:ea/@typeface). 不设的话 viewer 会用 latin 字体 fallback,
  中文渲染成豆腐 (□).
- **优先用主 prompt "## 运行环境"段列出的"已装 CJK 字体"清单里的名字** — 那是系统真装了
  的, 用户机上 viewer 不会 fallback 失败.
- **清单为空** → 调 `mcp__pentaloom__install_noto_sans_sc` 装 Noto Sans SC,
  用户授权后再继续. 不要让用户手动 brew. 装完后 verify autofix 会自动注 Noto Sans SC.
- 写 east_asian_name 的代码模板 (中文 run 都得过这层):

  ```python
  from lxml import etree
  NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
  def set_ea(run, font_name):
      rPr = run._r.find(f"{NS}rPr")
      if rPr is None:
          rPr = etree.SubElement(run._r, f"{NS}rPr")
          run._r.insert(0, rPr)
      ea = rPr.find(f"{NS}ea")
      if ea is None:
          ea = etree.SubElement(rPr, f"{NS}ea")
      ea.set("typeface", font_name)
  ```

- 偷懒不写 east_asian_name → autofix=True 兜底注入字体, 但**不要依赖兜底代替正确写法** —
  先写对, autofix 是降级方案.

## 强制 verify SOP (重点)

- 永远不可跳过工作流第 6 步的 verify. 哪怕你"看着脚本觉得没问题", 也得跑 verify.
- file_verify 报 font_tofu (字体豆腐) → autofix=True 自动注入, 不手改 XML.
- file_verify 报 geometry_overflow (几何越界) → 改脚本调 shape 的 left/top/width/height.
  **不要 clamp 到边界** (会破坏布局意图). 重新算坐标, 重新跑 5-7.
- file_verify 报 empty_slide (空 slide, warning 级) → 检查是脚本漏了内容, 还是真的设计成
  过渡页. 不强制阻断, 但 ≥ 5 页 PPT 出现 ≥ 2 个空页一般是脚本 bug.
- 无论用户多催, 没看到 blocking_count=0 就回"PPT 做好了"是错的.

## 交付时给用户看的内容

完成后回一段:

- PPT 文件绝对路径
- 页数 / 大致内容概览 (一两句)
- verify 结果 (blocking=0, warning=N, 注入的字体名)
- 用户怎么打开它 (open / start / xdg-open 之类提示一下即可)

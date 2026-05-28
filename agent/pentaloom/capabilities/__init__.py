"""PentaLoom 五项能力 (capabilities).

主 agent 直接调用这些模块, 不再走 SDK subagent 派发.

每项一个子目录:
  - file/      文件读写 / 文档解析 / 文档校验  (已实现)
  - app_gen/   应用 / 项目脚手架生成           (待实现)
  - browser/   浏览器自动化                    (待实现)
  - computer/  桌面操作 (截屏 / 键鼠 / 窗口)   (待实现)
  - search/    web 搜索 / 知识检索             (待实现)

各能力的入口在 tools/ 下注册成 MCP 工具暴露给 LLM, 这里只放纯实现.
"""

"""PentaLoom 五瓣 subagent.

ALL_AGENTS 给 PentaLoom 主类直接传:
    async with PentaLoom(agents=ALL_AGENTS) as pl: ...

也可以挑着用:
    async with PentaLoom(agents={"file": FILE_OPS_AGENT}) as pl: ...

子目录结构 (每个 agent 一个目录, 内部可拆 prompts/tools/...):
  - file/      已实现 (file_ops)
  - app_gen/   待实现
  - browser/   待实现
  - computer/  待实现
  - search/    待实现
"""

from pentaloom.agents.file import FILE_OPS_AGENT

ALL_AGENTS = {
    "file": FILE_OPS_AGENT,
}

__all__ = ["ALL_AGENTS", "FILE_OPS_AGENT"]

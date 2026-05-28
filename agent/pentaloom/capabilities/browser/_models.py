"""browser-use 数据模型.

工具返回值统一走这几个 Pydantic, 由 tools/browser.py 序列化成 JSON
塞进 SDK tool_result. 字段名直接给 LLM 看, 不缩写.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class InstallStepResult(BaseModel):
    """install_browser_use 一步的结果."""

    step: str  # "check" | "install" | "chromium"
    success: bool
    message: str
    next_step: str | None = None  # "install" / "chromium" / None (= 装完)


class BrowserUseResult(BaseModel):
    """browser_use 一次 CLI 调用的结果. output 已含 STOP 前缀 (state/switch)."""

    command: str
    output: str


class BrowserSessionInfoResult(BaseModel):
    """browser_use_session_info 给"生成脚本"路径用的常量集.

    callers 必须自己检查 cookies_path 是否存在 — 工具只算路径不验证.
    """

    session_name: str
    profile: str | None = None
    headed: bool = False
    cdp_url: str | None = None
    connect: bool = False
    browser: str | None = None
    cookies_path: str


class StoredSessionConfig(BaseModel):
    """落盘 session.json 的内容. open 时按 explicit flag 写, 后续动作重放."""

    headed: bool = False
    profile: str | None = None
    cdp_url: str | None = None
    connect: bool = False
    browser: str | None = None

    def merged_with(self, explicit: "StoredSessionConfig") -> "StoredSessionConfig":
        """显式参数覆盖落盘值. bool False 不算"显式给了" — 跟 setdefault 语义对齐."""
        data = self.model_dump()
        for k, v in explicit.model_dump().items():
            if isinstance(v, bool):
                if v:
                    data[k] = v
            elif v is not None:
                data[k] = v
        return StoredSessionConfig(**data)

    def to_cli_args(self, session_name: str) -> list[str]:
        """转成 CLI flag list. 顺序: [--headed?] --session NAME [--profile X] ..."""
        args: list[str] = ["--session", session_name]
        if self.headed:
            args.insert(0, "--headed")
        if self.profile:
            args.extend(["--profile", self.profile])
        if self.cdp_url:
            args.extend(["--cdp-url", self.cdp_url])
        if self.connect:
            args.append("--connect")
        if self.browser:
            args.extend(["--browser", self.browser])
        return args

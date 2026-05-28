"""browser 能力: 通过 browser-use CLI 子进程驱动浏览器自动化.

调用方走 tools/browser.py 的 in-process MCP 工具, 本模块只提供纯函数:
  - _detect: 跨平台 Chrome / Chromium 探测
  - _paths: session 数据落盘路径 + session 名生成
  - _models: Pydantic 数据模型 (工具返回值)
  - _command: CLI 命令解析 / 规范化 / session 参数注入
"""

from pentaloom.capabilities.browser._command import (
    build_session_args,
    extract_action_verb,
    is_state_command,
    is_switch_command,
    prepare_browser_command,
)
from pentaloom.capabilities.browser._detect import (
    is_browser_available,
    is_chromium_installed,
    is_system_chrome_installed,
)
from pentaloom.capabilities.browser._models import (
    BrowserSessionInfoResult,
    BrowserUseResult,
    InstallStepResult,
    StoredSessionConfig,
)
from pentaloom.capabilities.browser._paths import (
    compute_session_name,
    load_session_config,
    save_session_config,
    session_config_path,
    session_cookies_path,
    session_data_dir,
)

__all__ = [
    "BrowserSessionInfoResult",
    "BrowserUseResult",
    "InstallStepResult",
    "StoredSessionConfig",
    "build_session_args",
    "compute_session_name",
    "extract_action_verb",
    "is_browser_available",
    "is_chromium_installed",
    "is_state_command",
    "is_switch_command",
    "is_system_chrome_installed",
    "load_session_config",
    "prepare_browser_command",
    "save_session_config",
    "session_config_path",
    "session_cookies_path",
    "session_data_dir",
]

"""PentaLoom 自定义 MCP 工具 + 权限审批中枢."""

from pentaloom.tools.workspace import (
    BASH_TOOL_NAME,
    FULL_TOOL_NAME as REQUEST_WORKSPACE_DIR_TOOL_NAME,
    HITL_TOOL_NAMES,
    REGISTRY as PERMISSION_REGISTRY,
    WORKSPACE_MCP_SERVER,
    WORKSPACE_MCP_SERVER_NAME,
    build_hitl_hooks,
    make_can_use_tool,
)

# 向后兼容旧 import 名 (LoomPool / routers 还在用 WORKSPACE_REGISTRY).
WORKSPACE_REGISTRY = PERMISSION_REGISTRY

__all__ = [
    "BASH_TOOL_NAME",
    "HITL_TOOL_NAMES",
    "PERMISSION_REGISTRY",
    "REQUEST_WORKSPACE_DIR_TOOL_NAME",
    "WORKSPACE_MCP_SERVER",
    "WORKSPACE_MCP_SERVER_NAME",
    "WORKSPACE_REGISTRY",
    "build_hitl_hooks",
    "make_can_use_tool",
]

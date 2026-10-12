"""ToolRef -> MAF tool.

=============  =================================================================
``function``   imported ``module:attr``; plain callables are wrapped with ``@tool``
``mcp``        ``MCPStreamableHTTPTool`` restricted to the spec's ``allow`` list.
               If the caller passes connected tools (``mcp_tools={service: tool}``,
               as ``a2a_server.py`` does) those are used; otherwise an
               *unconnected* tool is returned and the caller must connect it.
               A *list* of plain tools may stand in for a service (tests /
               in-process runs); it is filtered to ``allow`` the same way.
``a2a_peer``   ``consult_<node>`` agent-as-tool (depth-guarded, soft-failing)
=============  =================================================================

Least privilege (ISTR rule 5) holds by construction: an agent receives exactly
the tools its spec lists, nothing inherited, nothing global.
"""
from __future__ import annotations

from typing import Any

from agent_framework import FunctionTool, MCPStreamableHTTPTool, tool

from src.factory.loader import import_ref
from src.factory.spec import A2APeerToolRef, AgentSpec, FunctionToolRef, McpToolRef
from src.integrations.mcp_clients import make_mcp_tool
from src.tools.collaboration_tools import make_peer_tool


class ToolResolutionError(ValueError):
    pass


class ToolResolver:
    def __init__(self, peer_descriptions: dict[str, str] | None = None):
        # node -> description, used to describe consult_<node> tools to the model.
        self.peer_descriptions = peer_descriptions or {}

    def resolve(self, spec: AgentSpec, mcp_tools: dict[str, Any] | None = None) -> list[Any]:
        tools: list[Any] = []
        for ref in spec.tools:
            resolved = self.resolve_one(spec, ref, mcp_tools or {})
            tools.extend(resolved if isinstance(resolved, list) else [resolved])
        return tools

    def resolve_one(self, spec: AgentSpec, ref: Any, mcp_tools: dict[str, Any]) -> Any:
        if isinstance(ref, FunctionToolRef):
            return self._function(spec, ref)
        if isinstance(ref, McpToolRef):
            return self._mcp(spec, ref, mcp_tools)
        if isinstance(ref, A2APeerToolRef):
            return self._peer(ref)
        raise ToolResolutionError(f"[{spec.name}] unsupported tool ref: {ref!r}")

    @staticmethod
    def _function(spec: AgentSpec, ref: FunctionToolRef) -> Any:
        try:
            obj = import_ref(ref.ref)
        except Exception as exc:
            raise ToolResolutionError(f"[{spec.name}] cannot import function tool '{ref.ref}': {exc}") from exc
        if isinstance(obj, FunctionTool):
            return obj
        if callable(obj):
            return tool(obj)
        raise ToolResolutionError(f"[{spec.name}] '{ref.ref}' is not callable")

    @staticmethod
    def _mcp(spec: AgentSpec, ref: McpToolRef, mcp_tools: dict[str, Any]) -> Any:
        provided = mcp_tools.get(ref.service)
        if provided is None:
            return make_mcp_tool(ref.service, ref.allow, owner=spec.name)
        if isinstance(provided, list):
            # In-process substitutes (tests / DevUI): still filtered to the allow list.
            return [t for t in provided if getattr(t, "name", None) in set(ref.allow)]
        if isinstance(provided, MCPStreamableHTTPTool):
            allowed = set(getattr(provided, "allowed_tools", None) or [])
            if allowed and not allowed <= set(ref.allow):
                raise ToolResolutionError(
                    f"[{spec.name}] connected '{ref.service}' tool allows {sorted(allowed)}, "
                    f"wider than the spec allow list {sorted(ref.allow)}"
                )
        return provided

    def _peer(self, ref: A2APeerToolRef) -> FunctionTool:
        description = ref.description or self.peer_descriptions.get(ref.node, f"Mesh agent '{ref.node}'.")
        return make_peer_tool(ref.node, description, max_depth=ref.max_depth)

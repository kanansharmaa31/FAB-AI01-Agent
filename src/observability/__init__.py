"""Observability: logging, MAF-native OpenTelemetry, W3C baggage, optional Opik."""
from src.observability.logging_config import (
    CAT_A2A,
    CAT_AGENT,
    CAT_FACTORY,
    CAT_MCP,
    CAT_SYSTEM,
    CAT_TOOLS,
    CAT_WORKFLOW,
    configure_logging,
    get_logger,
)
from src.observability.setup import setup_observability

__all__ = [
    "CAT_A2A", "CAT_AGENT", "CAT_FACTORY", "CAT_MCP", "CAT_SYSTEM", "CAT_TOOLS", "CAT_WORKFLOW",
    "configure_logging", "get_logger", "setup_observability",
]

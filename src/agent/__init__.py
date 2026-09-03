"""
Agent package for Mandate Recovery Agent.
Exports core decision loop and reasoning components.
"""
from src.agent.agent_loop import process_mandate_failure
from src.agent.context_builder import build_agent_context, format_context_for_prompt
from src.agent.gemini_client import call_gemini_turn, AgentTurnResponse
from src.agent.tool_registry import (
    dispatch_tool_call,
    get_mandate_history,
    get_notification_history,
    ACTION_TOOLS_MAP,
)

__all__ = [
    "process_mandate_failure",
    "build_agent_context",
    "format_context_for_prompt",
    "call_gemini_turn",
    "AgentTurnResponse",
    "dispatch_tool_call",
    "get_mandate_history",
    "get_notification_history",
    "ACTION_TOOLS_MAP",
]

"""
Tool Registry for Mandate Recovery Agent.
Declares callable tools and schemas for Google Gemini function calling.
Routes all 5 action tools through the compliance audit logger.
"""
from typing import Any, Callable
from src.tools import (
    schedule_retry,
    send_pre_debit_notice,
    send_recovery_nudge,
    log_promise_to_pay,
    escalate_to_human,
)
from src.guardrails.audit_logger import execute_tool_with_logging
from src.database import get_notifications_for_mandate, get_connection


# --- Read-Only Context Tools ---

def get_mandate_history(mandate_id: str) -> dict[str, Any]:
    """
    Read-only tool: retrieve prior retry attempts and details for a mandate.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM mandate_failures WHERE mandate_id = ?",
        (mandate_id,)
    ).fetchone()
    conn.close()

    if not row:
        return {"status": "not_found", "mandate_id": mandate_id}

    return {
        "mandate_id": mandate_id,
        "amount_paise": row["amount_paise"],
        "decline_reason": row["decline_reason"],
        "prior_retry_count": row["prior_retry_count"],
        "mandate_start_date": row["mandate_start_date"],
        "mandate_end_date": row["mandate_end_date"]
    }


def get_notification_history(mandate_id: str) -> dict[str, Any]:
    """
    Read-only tool: retrieve prior notifications and PDNs logged for a mandate.
    """
    notifs = get_notifications_for_mandate(mandate_id)
    return {
        "mandate_id": mandate_id,
        "count": len(notifs),
        "notifications": notifs
    }


# Map of all available action tool implementations
ACTION_TOOLS_MAP: dict[str, Callable[..., dict[str, Any]]] = {
    "schedule_retry": schedule_retry,
    "send_pre_debit_notice": send_pre_debit_notice,
    "send_recovery_nudge": send_recovery_nudge,
    "log_promise_to_pay": log_promise_to_pay,
    "escalate_to_human": escalate_to_human,
}

# Map of read-only tools
READ_ONLY_TOOLS_MAP: dict[str, Callable[..., dict[str, Any]]] = {
    "get_mandate_history": get_mandate_history,
    "get_notification_history": get_notification_history,
}


def dispatch_tool_call(
    tool_name: str,
    tool_args: dict[str, Any],
    agent_reasoning: str,
    record_context: dict[str, Any]
) -> dict[str, Any]:
    """
    Safely dispatches a function call requested by the model.
    - Read-only tools execute without modifying state.
    - Action tools are strictly routed through execute_tool_with_logging (guarded).
    """
    if tool_name in READ_ONLY_TOOLS_MAP:
        try:
            return READ_ONLY_TOOLS_MAP[tool_name](**tool_args)
        except Exception as e:
            return {"error": f"Failed to execute read-only tool '{tool_name}': {str(e)}"}

    if tool_name in ACTION_TOOLS_MAP:
        fn = ACTION_TOOLS_MAP[tool_name]
        return execute_tool_with_logging(
            tool_name=tool_name,
            tool_fn=fn,
            tool_args=tool_args,
            agent_reasoning=agent_reasoning,
            record_context=record_context
        )

    return {
        "error": f"Tool '{tool_name}' does not exist. Only the 5 authorized action tools and 2 context tools are permitted."
    }

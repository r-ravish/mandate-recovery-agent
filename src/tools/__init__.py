"""
Tools package for Mandate Recovery Agent.
Exports the exact, locked set of 5 executable tools.
Structural non-actions (cancel, modify amount, new mandate) do NOT exist.
"""
from src.tools.retry_scheduler import schedule_retry
from src.tools.pre_debit_notifier import send_pre_debit_notice
from src.tools.recovery_nudge import send_recovery_nudge
from src.tools.promise_to_pay import log_promise_to_pay
from src.tools.escalation import escalate_to_human

# Explicitly exposed tools
ALL_TOOLS = {
    "schedule_retry": schedule_retry,
    "send_pre_debit_notice": send_pre_debit_notice,
    "send_recovery_nudge": send_recovery_nudge,
    "log_promise_to_pay": log_promise_to_pay,
    "escalate_to_human": escalate_to_human,
}

__all__ = [
    "schedule_retry",
    "send_pre_debit_notice",
    "send_recovery_nudge",
    "log_promise_to_pay",
    "escalate_to_human",
    "ALL_TOOLS",
]

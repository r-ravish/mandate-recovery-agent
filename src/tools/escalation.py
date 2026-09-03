"""
Human Escalation Tool for Mandate Recovery Agent.
Safely transfers cases requiring human intervention or discretionary judgment
(e.g., non-recoverable failures, customer disputes, limit adjustments).
"""
from datetime import datetime
from typing import Any, Optional
from src.database import get_connection


def escalate_to_human(
    record_id: str,
    mandate_id: str,
    reason: str,
    priority: str = "medium",
    recommended_human_action: Optional[str] = None
) -> dict[str, Any]:
    """
    Escalates the mandate failure case to a human operations specialist.
    
    Parameters:
    - record_id: Failure record identifier
    - mandate_id: Mandate identifier
    - reason: Concrete explanation of why automation cannot safely resolve this
    - priority: low, medium, high, urgent
    - recommended_human_action: Proposed next step for human agent
    """
    if not reason or not reason.strip():
        return {
            "success": False,
            "error": "Escalation requires a clear, non-empty justification reason.",
            "mandate_id": mandate_id
        }

    now_iso = datetime.now().isoformat()
    ticket_id = f"ESC-{mandate_id[-6:]}-{int(datetime.now().timestamp()) % 10000:04d}"

    # Record escalation outcome into agent_decisions
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO agent_decisions 
        (record_id, mandate_id, diagnosed_reason, chosen_action, action_arguments,
         reasoning, steps_taken, timestamp, compliance_violations, success)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        record_id,
        mandate_id,
        "ESCALATED",
        "escalate_to_human",
        f'{{"reason": "{reason}", "priority": "{priority}"}}',
        reason,
        1,
        now_iso,
        "[]",
        1
    ))
    conn.commit()
    conn.close()

    return {
        "success": True,
        "action": "escalate_to_human",
        "status": "handed_off_to_human",
        "ticket_id": ticket_id,
        "record_id": record_id,
        "mandate_id": mandate_id,
        "escalation_reason": reason,
        "priority": priority,
        "recommended_human_action": recommended_human_action or "Review mandate status and contact customer directly",
        "timestamp": now_iso,
        "message": f"Case escalated to human collections desk. Ticket {ticket_id} opened ({priority} priority)."
    }

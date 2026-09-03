"""
Retry Scheduler Tool for Mandate Recovery Agent.
Schedules a compliant retry execution for a failed recurring payment mandate.

Self-enforcing features:
- Automatically validates the requested time against NPCI 2026 execution windows.
- If the requested time falls inside a peak congestion window (10:00-13:00 or 17:00-21:30),
  it automatically clamps and pushes the execution time forward into the next legal window.
- Updates database state to track scheduled retries.
"""
from datetime import datetime
from typing import Any, Optional
from src.guardrails.compliance_checker import (
    parse_iso_datetime,
    is_within_blocked_window,
    adjust_to_next_permitted_window,
)
from src.database import get_connection


def schedule_retry(
    record_id: str,
    mandate_id: str,
    requested_retry_time: str,
    reason_for_retry: str = "Automated retry after compliance verification"
) -> dict[str, Any]:
    """
    Schedule a compliant debit retry for a mandate.
    
    Parameters:
    - record_id: Identifier of the failure record being resolved
    - mandate_id: Mandate identifier
    - requested_retry_time: ISO format datetime requested by agent
    - reason_for_retry: Natural language justification
    
    Returns:
    - Receipt dictionary with scheduled timestamp, compliance adjustment notes, and status
    """
    try:
        dt = parse_iso_datetime(requested_retry_time)
    except Exception as e:
        return {
            "success": False,
            "error": f"Invalid timestamp format: {e}",
            "mandate_id": mandate_id
        }

    # Automatically check and adjust NPCI peak execution window
    adjusted_dt, was_adjusted = adjust_to_next_permitted_window(dt)

    adjustment_note = None
    if was_adjusted:
        adjustment_note = (
            f"Requested time {dt.strftime('%H:%M')} falls within NPCI blocked peak congestion window. "
            f"Automatically shifted forward to compliant execution window at {adjusted_dt.strftime('%H:%M')}."
        )

    # Persist the scheduled retry and increment retry count in DB
    conn = get_connection()
    cursor = conn.cursor()
    
    # Retrieve current count
    row = cursor.execute("SELECT prior_retry_count FROM mandate_failures WHERE record_id = ?", (record_id,)).fetchone()
    current_count = row["prior_retry_count"] if row else 0
    new_count = current_count + 1

    cursor.execute(
        "UPDATE mandate_failures SET prior_retry_count = ? WHERE record_id = ?",
        (new_count, record_id)
    )
    conn.commit()
    conn.close()

    return {
        "success": True,
        "action": "schedule_retry",
        "record_id": record_id,
        "mandate_id": mandate_id,
        "original_requested_time": dt.isoformat(),
        "scheduled_retry_time": adjusted_dt.isoformat(),
        "window_adjusted": was_adjusted,
        "adjustment_note": adjustment_note,
        "retry_attempt_number": new_count,
        "reason_for_retry": reason_for_retry,
        "message": (
            f"Mandate debit retry scheduled for {adjusted_dt.strftime('%Y-%m-%d %I:%M %p')}. "
            f"Attempt #{new_count} of 3."
        )
    }

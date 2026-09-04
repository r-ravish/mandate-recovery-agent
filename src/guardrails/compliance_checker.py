"""
Compliance Checker for Mandate Recovery Agent.
Implements the 3 non-negotiable compliance rules from Phase 0:
1. Rule 1: Mandatory 24-hour advance Pre-Debit Notification (PDN) prior to any retry
2. Rule 2: NPCI Execution Window compliance (blocks retries during peak congestion:
   10:00 AM - 1:00 PM and 5:00 PM - 9:30 PM; auto-adjusts to compliant window)
3. Rule 3: Strict retry ceiling (maximum 3 retries per cycle)
Also enforces non-recoverable decline rules (mandate expired/revoked/unclassified/AFA).

Every check is deterministic Python code that raises or flags violations before tools execute.
"""
from datetime import datetime, time, timedelta
from typing import Optional, Any
from src.config import (
    MAX_RETRIES_PER_CYCLE,
    PRE_DEBIT_NOTICE_HOURS,
    BLOCKED_WINDOWS,
    NON_RECOVERABLE_REASONS,
)
from src.database import get_notifications_for_mandate, get_connection


def parse_iso_datetime(dt_str: str) -> datetime:
    """Parse ISO string to datetime, normalizing to offset-naive UTC for consistent comparisons."""
    from datetime import timezone
    clean_str = dt_str.replace("Z", "+00:00") if dt_str.endswith("Z") else dt_str
    dt = None
    try:
        dt = datetime.fromisoformat(clean_str)
    except ValueError:
        # Fallback for common date formats
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(clean_str, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        raise ValueError(f"Unable to parse datetime string: {dt_str}")

    # Strip tzinfo after converting to UTC so all comparisons in the system are naive-safe
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def is_within_blocked_window(dt: datetime) -> bool:
    """
    Check if a given datetime falls within NPCI's 2026 restricted execution windows:
    - 10:00 AM to 1:00 PM (10:00 to 13:00)
    - 5:00 PM to 9:30 PM (17:00 to 21:30)
    """
    t = dt.time()
    for start_str, end_str in BLOCKED_WINDOWS:
        s_h, s_m = map(int, start_str.split(":"))
        e_h, e_m = map(int, end_str.split(":"))
        start_time = time(s_h, s_m)
        end_time = time(e_h, e_m)
        if start_time <= t < end_time:
            return True
    return False


def adjust_to_next_permitted_window(dt: datetime) -> tuple[datetime, bool]:
    """
    If dt falls in an NPCI blocked window, automatically push it forward
    to the beginning of the next permitted window.
    
    Returns: (adjusted_datetime, was_adjusted_flag)
    """
    if not is_within_blocked_window(dt):
        return dt, False

    t = dt.time()
    adjusted = dt

    # Window 1: 10:00 AM - 1:00 PM -> push to 1:05 PM (13:05)
    t_10 = time(10, 0)
    t_13 = time(13, 0)
    if t_10 <= t < t_13:
        adjusted = dt.replace(hour=13, minute=5, second=0, microsecond=0)
        return adjusted, True

    # Window 2: 5:00 PM - 9:30 PM -> push to 9:35 PM (21:35)
    t_17 = time(17, 0)
    t_21_30 = time(21, 30)
    if t_17 <= t < t_21_30:
        adjusted = dt.replace(hour=21, minute=35, second=0, microsecond=0)
        return adjusted, True

    return dt, False


def check_pre_debit_notification(
    mandate_id: str,
    proposed_retry_time: datetime,
    record_context: Optional[dict[str, Any]] = None
) -> tuple[bool, Optional[str]]:
    """
    Rule 1 Verification:
    Checks whether a Pre-Debit Notification (PDN) was sent to the customer
    at least 24 hours prior to the proposed retry execution time.
    """
    # 1. Check in-memory record prior_notifications first
    notifications = []
    if record_context and record_context.get("prior_notifications"):
        notifications.extend(record_context["prior_notifications"])

    # 2. Check notification_log table in database
    db_notifs = get_notifications_for_mandate(mandate_id)
    notifications.extend(db_notifs)

    # Search for a pre-debit notification sent >= 24h prior to proposed_retry_time
    valid_pdn_found = False
    latest_pdn_time: Optional[datetime] = None

    for notif in notifications:
        notif_type = notif.get("notification_type")
        if notif_type == "pre_debit_notice":
            sent_at_raw = notif.get("sent_at")
            if not sent_at_raw:
                continue
            try:
                sent_at = parse_iso_datetime(str(sent_at_raw))
            except ValueError:
                continue

            if latest_pdn_time is None or sent_at > latest_pdn_time:
                latest_pdn_time = sent_at

            # Calculate advance notice interval
            advance_seconds = (proposed_retry_time - sent_at).total_seconds()
            required_seconds = PRE_DEBIT_NOTICE_HOURS * 3600

            # Must be at least 24h prior, and not older than 7 days
            if advance_seconds >= required_seconds and advance_seconds <= 7 * 86400:
                valid_pdn_found = True
                break

    if valid_pdn_found:
        return True, None

    if latest_pdn_time:
        hours_diff = (proposed_retry_time - latest_pdn_time).total_seconds() / 3600.0
        return False, (
            f"Pre-Debit Notification (PDN) sent only {hours_diff:.1f} hours prior. "
            f"Mandatory minimum advance notice is {PRE_DEBIT_NOTICE_HOURS} hours before retry."
        )
    else:
        return False, (
            f"No Pre-Debit Notification (PDN) has been recorded for mandate {mandate_id}. "
            f"Under NPCI rules, a notice must be logged at least {PRE_DEBIT_NOTICE_HOURS} hours prior to any debit."
        )


def check_retry_ceiling(
    mandate_id: str,
    current_retry_count: int
) -> tuple[bool, Optional[str]]:
    """
    Rule 3 Verification:
    Maximum 3 retries allowed per mandate cycle.
    """
    if current_retry_count >= MAX_RETRIES_PER_CYCLE:
        return False, (
            f"Retry ceiling of {MAX_RETRIES_PER_CYCLE} attempts already reached for mandate {mandate_id}. "
            f"Further retries are blocked; case must be escalated to a human."
        )
    return True, None


def check_recoverability(decline_reason: str) -> tuple[bool, Optional[str]]:
    """
    Verifies that the decline reason is permissible to retry.
    Non-recoverable reasons (expired, revoked, unclassified, AFA limit exceeded) cannot be retried.
    """
    clean_reason = decline_reason.upper().strip()
    if clean_reason in NON_RECOVERABLE_REASONS or clean_reason == "AFA_LIMIT_EXCEEDED":
        return False, (
            f"Decline reason '{decline_reason}' is structurally non-recoverable. "
            f"Retries are prohibited; mandate requires human escalation."
        )
    return True, None


def validate_compliance(
    tool_name: str,
    tool_args: dict[str, Any],
    record_context: dict[str, Any]
) -> tuple[bool, list[str]]:
    """
    Central gatekeeper: validates ANY attempted tool call against all compliance rules.
    Returns: (is_compliant, list_of_violations)
    """
    violations: list[str] = []
    mandate_id = str(tool_args.get("mandate_id") or record_context.get("mandate_id", "unknown"))

    if tool_name == "schedule_retry":
        # Check 1: Recoverability
        decline_reason = record_context.get("decline_reason", "UNCLASSIFIED")
        rec_ok, rec_err = check_recoverability(decline_reason)
        if not rec_ok and rec_err:
            violations.append(rec_err)

        # Check 2: Retry ceiling
        retry_count = int(record_context.get("prior_retry_count", 0))
        ceil_ok, ceil_err = check_retry_ceiling(mandate_id, retry_count)
        if not ceil_ok and ceil_err:
            violations.append(ceil_err)

        # Check 3: PDN advance notice
        requested_time_raw = tool_args.get("requested_retry_time")
        if requested_time_raw:
            try:
                requested_dt = parse_iso_datetime(str(requested_time_raw))
                pdn_ok, pdn_err = check_pre_debit_notification(mandate_id, requested_dt, record_context)
                if not pdn_ok and pdn_err:
                    violations.append(pdn_err)
            except ValueError as e:
                violations.append(f"Invalid requested_retry_time: {e}")
        else:
            violations.append("schedule_retry requires a valid 'requested_retry_time' argument.")

    elif tool_name == "send_recovery_nudge":
        # Check spam cap: max 2 nudges per mandate
        notifs = get_notifications_for_mandate(mandate_id)
        prior_nudges = [n for n in notifs if n.get("notification_type") == "recovery_nudge"]
        if len(prior_nudges) >= 2:
            violations.append(
                f"Customer harassment guardrail: Maximum of 2 recovery nudges per cycle already sent for {mandate_id}."
            )

    return len(violations) == 0, violations

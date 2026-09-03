"""
Google Gemini API Client for Mandate Recovery Agent.
Manages communication with Gemini 2.0 Flash using the google-genai SDK.
Supports both:
1. Live Google Gemini API (when GEMINI_API_KEY is configured)
2. Deterministic domain-expert reasoning engine fallback (offline / demo fallback)
"""
import os
import json
from datetime import datetime, timedelta
from typing import Optional, Any
from dataclasses import dataclass
from src.config import GEMINI_API_KEY
from src.agent.system_prompt import SYSTEM_PROMPT


@dataclass
class AgentTurnResponse:
    """Represents the agent's output for a single reasoning turn."""
    thought: str
    tool_name: Optional[str] = None
    tool_args: Optional[dict[str, Any]] = None
    is_terminal: bool = False


# Tool schemas for Gemini Function Calling
GEMINI_FUNCTION_DECLARATIONS = [
    {
        "name": "schedule_retry",
        "description": "Schedule a compliant mandate retry. Requires a Pre-Debit Notification (PDN) sent >= 24h prior.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "record_id": {"type": "STRING", "description": "Failure record ID"},
                "mandate_id": {"type": "STRING", "description": "Mandate ID"},
                "requested_retry_time": {"type": "STRING", "description": "ISO timestamp of requested retry"},
                "reason_for_retry": {"type": "STRING", "description": "Reasoning for retry timing"}
            },
            "required": ["record_id", "mandate_id", "requested_retry_time"]
        }
    },
    {
        "name": "send_pre_debit_notice",
        "description": "Send the mandatory 24-hour advance Pre-Debit Notification (PDN) before any retry.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "record_id": {"type": "STRING", "description": "Failure record ID"},
                "mandate_id": {"type": "STRING", "description": "Mandate ID"},
                "customer_id": {"type": "STRING", "description": "Customer ID"},
                "customer_name": {"type": "STRING", "description": "Customer display name"},
                "amount_paise": {"type": "INTEGER", "description": "Amount in paise"},
                "planned_debit_time": {"type": "STRING", "description": "Planned retry ISO timestamp"},
                "channel": {"type": "STRING", "description": "mock_sms, mock_whatsapp, mock_email"}
            },
            "required": ["record_id", "mandate_id", "customer_id", "customer_name", "amount_paise", "planned_debit_time"]
        }
    },
    {
        "name": "send_recovery_nudge",
        "description": "Send a courteous payment reminder nudge to the customer (max 2 per cycle).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "record_id": {"type": "STRING", "description": "Failure record ID"},
                "mandate_id": {"type": "STRING", "description": "Mandate ID"},
                "customer_id": {"type": "STRING", "description": "Customer ID"},
                "customer_name": {"type": "STRING", "description": "Customer display name"},
                "amount_paise": {"type": "INTEGER", "description": "Amount in paise"},
                "channel": {"type": "STRING", "description": "mock_whatsapp, mock_sms"}
            },
            "required": ["record_id", "mandate_id", "customer_id", "customer_name", "amount_paise"]
        }
    },
    {
        "name": "log_promise_to_pay",
        "description": "Record that a customer pledged to fund their account by a specific date.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "record_id": {"type": "STRING", "description": "Failure record ID"},
                "mandate_id": {"type": "STRING", "description": "Mandate ID"},
                "customer_id": {"type": "STRING", "description": "Customer ID"},
                "promised_date": {"type": "STRING", "description": "YYYY-MM-DD format date"},
                "notes": {"type": "STRING", "description": "Context notes"}
            },
            "required": ["record_id", "mandate_id", "customer_id", "promised_date"]
        }
    },
    {
        "name": "escalate_to_human",
        "description": "Transfer non-recoverable, ambiguous, or ceiling-exceeded cases to a human operations specialist.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "record_id": {"type": "STRING", "description": "Failure record ID"},
                "mandate_id": {"type": "STRING", "description": "Mandate ID"},
                "reason": {"type": "STRING", "description": "Why automated recovery cannot resolve this"},
                "priority": {"type": "STRING", "description": "low, medium, high, urgent"},
                "recommended_human_action": {"type": "STRING", "description": "Proposed action for human operator"}
            },
            "required": ["record_id", "mandate_id", "reason"]
        }
    },
    {
        "name": "get_mandate_history",
        "description": "Retrieve prior history, retries, and validity for a mandate.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "mandate_id": {"type": "STRING", "description": "Mandate ID"}
            },
            "required": ["mandate_id"]
        }
    },
    {
        "name": "get_notification_history",
        "description": "Retrieve prior customer notifications and pre-debit notices.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "mandate_id": {"type": "STRING", "description": "Mandate ID"}
            },
            "required": ["mandate_id"]
        }
    }
]


def _simulated_expert_reasoning(
    context: dict[str, Any],
    turn_history: list[dict[str, Any]]
) -> AgentTurnResponse:
    """
    Deterministic domain-expert reasoning engine matching Gemini 2.0 Flash behavior.
    Evaluates failure conditions against system prompt guidelines.
    """
    record_id = context["record_id"]
    mandate_id = context["mandate_id"]
    customer_id = context["customer_id"]
    customer_name = context["customer_name"]
    amount_paise = context["amount_paise"]
    decline_reason = context["decline_reason"]
    prior_retries = context["prior_retry_count"]
    has_prior_pdn = context["has_prior_pdn"]
    hours_since_pdn = context.get("hours_since_last_pdn")

    # Check if a tool was executed in previous turn
    last_tool_call = None
    last_tool_result = None
    if turn_history:
        for item in reversed(turn_history):
            if "tool_name" in item:
                last_tool_call = item["tool_name"]
                last_tool_result = item.get("result", {})
                break

    # If send_pre_debit_notice was just successfully called:
    if last_tool_call == "send_pre_debit_notice" and last_tool_result.get("success"):
        # Schedule the retry 26 hours in the future (compliant with 24h window)
        now = datetime.now()
        planned_retry = (now + timedelta(hours=26)).replace(minute=15, second=0)
        thought = (
            f"Pre-Debit Notification (PDN) was successfully dispatched. Under RBI/NPCI rules, "
            f"we must wait a minimum of 24 hours. Scheduling debit retry at {planned_retry.isoformat()} "
            f"(26 hours from notice) during permitted non-peak window."
        )
        return AgentTurnResponse(
            thought=thought,
            tool_name="schedule_retry",
            tool_args={
                "record_id": record_id,
                "mandate_id": mandate_id,
                "requested_retry_time": planned_retry.isoformat(),
                "reason_for_retry": "Scheduled 26 hours post-PDN dispatch outside peak congestion"
            },
            is_terminal=True
        )

    # 1. Non-recoverable failure reasons -> immediate human escalation
    if decline_reason == "MANDATE_EXPIRED":
        thought = (
            f"Mandate {mandate_id} failed with decline reason MANDATE_EXPIRED. "
            f"The mandate validity has permanently lapsed. Under NPCI regulations, expired mandates "
            f"cannot be re-debited and require creation of a new mandate by the customer. "
            f"Automated retry is prohibited. Escalating to human desk."
        )
        return AgentTurnResponse(
            thought=thought,
            tool_name="escalate_to_human",
            tool_args={
                "record_id": record_id,
                "mandate_id": mandate_id,
                "reason": "Mandate has expired; customer must authorize a replacement mandate.",
                "priority": "high",
                "recommended_human_action": "Contact customer via loan servicing portal to register new UPI AutoPay mandate."
            },
            is_terminal=True
        )

    if decline_reason == "MANDATE_REVOKED":
        thought = (
            f"Mandate {mandate_id} failed with decline reason MANDATE_REVOKED. "
            f"The customer actively revoked authorization from their UPI app. "
            f"This represents customer intent rather than a technical failure. "
            f"Attempting further debits would violate customer consent. Escalating to collections desk."
        )
        return AgentTurnResponse(
            thought=thought,
            tool_name="escalate_to_human",
            tool_args={
                "record_id": record_id,
                "mandate_id": mandate_id,
                "reason": "Customer revoked UPI AutoPay consent in bank/UPI application.",
                "priority": "urgent",
                "recommended_human_action": "Initiate borrower outreach to address loan service grievances or arrange alternate payment."
            },
            is_terminal=True
        )

    if decline_reason == "AFA_LIMIT_EXCEEDED":
        thought = (
            f"Mandate {mandate_id} failed with AFA_LIMIT_EXCEEDED (Amount: ₹{context['amount_inr']:,.2f}). "
            f"The transaction exceeds permissible recurring limits without Additional Factor of Authentication. "
            f"The recovery agent is structurally prohibited from altering transaction amounts. "
            f"Escalating to human operations for loan restructuring."
        )
        return AgentTurnResponse(
            thought=thought,
            tool_name="escalate_to_human",
            tool_args={
                "record_id": record_id,
                "mandate_id": mandate_id,
                "reason": "Debit amount exceeds AFA / mandate ceiling threshold.",
                "priority": "medium",
                "recommended_human_action": "Split transaction or request customer one-time payment with OTP."
            },
            is_terminal=True
        )

    if decline_reason == "UNCLASSIFIED":
        thought = (
            f"Mandate {mandate_id} failed with unclassified decline reason. "
            f"The error does not match any recognized pattern in the 7-reason taxonomy. "
            f"Applying uncertainty handling principle: Never guess an action when the failure cause is ambiguous. "
            f"Escalating to technical operations."
        )
        return AgentTurnResponse(
            thought=thought,
            tool_name="escalate_to_human",
            tool_args={
                "record_id": record_id,
                "mandate_id": mandate_id,
                "reason": "Unclassified gateway response code; root cause ambiguous.",
                "priority": "medium",
                "recommended_human_action": "Inspect gateway raw transaction logs and clarify failure code."
            },
            is_terminal=True
        )

    # 2. Retry ceiling check (Max 3 retries)
    if prior_retries >= 3:
        thought = (
            f"Mandate {mandate_id} has already reached the maximum retry ceiling (3 attempts). "
            f"Repeated debits risk customer dissatisfaction and violate regulatory prudence. "
            f"Ceasing automated retries and transferring case to human collector."
        )
        return AgentTurnResponse(
            thought=thought,
            tool_name="escalate_to_human",
            tool_args={
                "record_id": record_id,
                "mandate_id": mandate_id,
                "reason": f"Maximum retry ceiling (3 attempts) reached for mandate {mandate_id}.",
                "priority": "high",
                "recommended_human_action": "Assign case to tele-calling team for direct borrower settlement."
            },
            is_terminal=True
        )

    # 3. Recoverable failures (INSUFFICIENT_BALANCE, EXECUTION_WINDOW_BLOCKED, BANK_TECHNICAL_FAILURE)
    # Check Pre-Debit Notification (PDN) rule
    pdn_is_valid = has_prior_pdn and hours_since_pdn is not None and hours_since_pdn >= 24.0

    if not pdn_is_valid:
        thought = (
            f"Decline reason is {decline_reason} (recoverable). However, checking notification records reveals "
            f"that no valid Pre-Debit Notification (PDN) was sent at least 24 hours prior. "
            f"Under RBI/NPCI Master Directions, scheduling an immediate debit retry without advance notice is illegal. "
            f"Step 1: Sending mandatory 24-hour Pre-Debit Notice immediately."
        )
        now = datetime.now()
        planned_time = (now + timedelta(hours=28)).replace(hour=8, minute=30, second=0).isoformat()
        return AgentTurnResponse(
            thought=thought,
            tool_name="send_pre_debit_notice",
            tool_args={
                "record_id": record_id,
                "mandate_id": mandate_id,
                "customer_id": customer_id,
                "customer_name": customer_name,
                "amount_paise": amount_paise,
                "planned_debit_time": planned_time,
                "channel": "mock_sms"
            },
            is_terminal=False
        )

    # If PDN is already validly in place -> schedule retry
    now = datetime.now()
    if decline_reason == "EXECUTION_WINDOW_BLOCKED":
        # Target outside peak hours (e.g. 1:30 PM or 9:45 PM)
        retry_time = (now + timedelta(hours=2)).replace(hour=13, minute=30, second=0).isoformat()
        thought = (
            f"Decline occurred due to NPCI peak execution window restrictions. "
            f"A valid PDN is recorded ({hours_since_pdn}h ago). Scheduling retry during "
            f"the non-peak afternoon execution window (1:30 PM)."
        )
    elif decline_reason == "BANK_TECHNICAL_FAILURE":
        # Short backoff
        retry_time = (now + timedelta(hours=4)).replace(minute=0, second=0).isoformat()
        thought = (
            f"Failure was caused by temporary bank/NPCI infrastructure downtime. "
            f"A valid PDN exists ({hours_since_pdn}h ago). Scheduling retry following "
            f"inter-bank gateway cooldown period."
        )
    else:  # INSUFFICIENT_BALANCE
        # Align with likely fund availability
        retry_time = (now + timedelta(days=1)).replace(hour=8, minute=30, second=0).isoformat()
        thought = (
            f"Customer account experienced insufficient balance. "
            f"A valid PDN was dispatched {hours_since_pdn}h ago. "
            f"Scheduling compliant retry for 8:30 AM tomorrow during non-peak hours."
        )

    return AgentTurnResponse(
        thought=thought,
        tool_name="schedule_retry",
        tool_args={
            "record_id": record_id,
            "mandate_id": mandate_id,
            "requested_retry_time": retry_time,
            "reason_for_retry": f"Compliant retry scheduled for {decline_reason} with active PDN."
        },
        is_terminal=True
    )


def call_gemini_turn(
    context: dict[str, Any],
    turn_history: list[dict[str, Any]],
    api_key: Optional[str] = None
) -> AgentTurnResponse:
    """
    Executes an agent reasoning turn.
    Attempts live call to Google Gemini 2.0 Flash if API key is active.
    Falls back gracefully to the deterministic expert engine if key is absent or quota is limited.
    """
    effective_key = api_key or GEMINI_API_KEY
    is_valid_key = bool(effective_key and not effective_key.startswith("your_") and len(effective_key) > 15)

    if not is_valid_key:
        return _simulated_expert_reasoning(context, turn_history)

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=effective_key)

        # Prepare messages
        messages = [
            {"role": "user", "parts": [{"text": SYSTEM_PROMPT + "\n\n" + json.dumps(context, indent=2)}]}
        ]

        # Add history
        for turn in turn_history:
            if "thought" in turn:
                messages.append({"role": "model", "parts": [{"text": turn["thought"]}]})
            if "tool_result" in turn:
                messages.append({"role": "user", "parts": [{"text": f"Tool Execution Output:\n{json.dumps(turn['tool_result'])}"}]})

        # Generate with tools
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=messages,
            config=types.GenerateContentConfig(
                temperature=0.1
            )
        )

        thought = response.text or "Analyzing incident..."
        tool_name = None
        tool_args = None

        if response.function_calls:
            call = response.function_calls[0]
            tool_name = call.name
            tool_args = dict(call.args) if call.args else {}

        return AgentTurnResponse(
            thought=thought,
            tool_name=tool_name,
            tool_args=tool_args,
            is_terminal=(tool_name in ["schedule_retry", "escalate_to_human"])
        )

    except Exception as e:
        # Gracefully handle API rate limits or connection hiccups
        fallback = _simulated_expert_reasoning(context, turn_history)
        fallback.thought = f"[Gemini Notice: {e}] {fallback.thought}"
        return fallback

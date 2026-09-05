"""
Groq API Client for Mandate Recovery Agent.
Uses Groq's OpenAI-compatible Chat Completions API (base: https://api.groq.com/openai/v1).
Supports both:
1. Live Groq inference (when GROQ_API_KEY is configured in .env)
2. Deterministic domain-expert reasoning engine fallback (offline / demo mode)
"""
import os
import json
from datetime import datetime, timedelta
from typing import Optional, Any
from dataclasses import dataclass
from src.config import GROQ_API_KEY
from src.agent.system_prompt import SYSTEM_PROMPT


@dataclass
class AgentTurnResponse:
    """Represents the agent's output for a single reasoning turn."""
    thought: str
    tool_name: Optional[str] = None
    tool_args: Optional[dict[str, Any]] = None
    is_terminal: bool = False


# Tool schemas in OpenAI-style function-calling format
# (Groq's API is OpenAI-compatible and uses identical schema structure)
OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "schedule_retry",
            "description": "Schedule a compliant mandate retry. Requires a Pre-Debit Notification (PDN) sent >= 24h prior.",
            "parameters": {
                "type": "object",
                "properties": {
                    "record_id": {"type": "string", "description": "Failure record ID"},
                    "mandate_id": {"type": "string", "description": "Mandate ID"},
                    "requested_retry_time": {"type": "string", "description": "ISO timestamp of requested retry"},
                    "reason_for_retry": {"type": "string", "description": "Reasoning for retry timing"}
                },
                "required": ["record_id", "mandate_id", "requested_retry_time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_pre_debit_notice",
            "description": "Send the mandatory 24-hour advance Pre-Debit Notification (PDN) before any retry.",
            "parameters": {
                "type": "object",
                "properties": {
                    "record_id": {"type": "string", "description": "Failure record ID"},
                    "mandate_id": {"type": "string", "description": "Mandate ID"},
                    "customer_id": {"type": "string", "description": "Customer ID"},
                    "customer_name": {"type": "string", "description": "Customer display name"},
                    "amount_paise": {"type": "integer", "description": "Amount in paise"},
                    "planned_debit_time": {"type": "string", "description": "Planned retry ISO timestamp"},
                    "channel": {"type": "string", "description": "mock_sms, mock_whatsapp, mock_email"}
                },
                "required": ["record_id", "mandate_id", "customer_id", "customer_name", "amount_paise", "planned_debit_time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_recovery_nudge",
            "description": "Send a courteous payment reminder nudge to the customer (max 2 per cycle).",
            "parameters": {
                "type": "object",
                "properties": {
                    "record_id": {"type": "string", "description": "Failure record ID"},
                    "mandate_id": {"type": "string", "description": "Mandate ID"},
                    "customer_id": {"type": "string", "description": "Customer ID"},
                    "customer_name": {"type": "string", "description": "Customer display name"},
                    "amount_paise": {"type": "integer", "description": "Amount in paise"},
                    "channel": {"type": "string", "description": "mock_whatsapp, mock_sms"}
                },
                "required": ["record_id", "mandate_id", "customer_id", "customer_name", "amount_paise"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "log_promise_to_pay",
            "description": "Record that a customer pledged to fund their account by a specific date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "record_id": {"type": "string", "description": "Failure record ID"},
                    "mandate_id": {"type": "string", "description": "Mandate ID"},
                    "customer_id": {"type": "string", "description": "Customer ID"},
                    "promised_date": {"type": "string", "description": "YYYY-MM-DD format date"},
                    "notes": {"type": "string", "description": "Context notes"}
                },
                "required": ["record_id", "mandate_id", "customer_id", "promised_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": "Transfer non-recoverable, ambiguous, or ceiling-exceeded cases to a human operations specialist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "record_id": {"type": "string", "description": "Failure record ID"},
                    "mandate_id": {"type": "string", "description": "Mandate ID"},
                    "reason": {"type": "string", "description": "Why automated recovery cannot resolve this"},
                    "priority": {"type": "string", "description": "low, medium, high, urgent"},
                    "recommended_human_action": {"type": "string", "description": "Proposed action for human operator"}
                },
                "required": ["record_id", "mandate_id", "reason"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_mandate_history",
            "description": "Retrieve prior history, retries, and validity for a mandate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mandate_id": {"type": "string", "description": "Mandate ID"}
                },
                "required": ["mandate_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_notification_history",
            "description": "Retrieve prior customer notifications and pre-debit notices.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mandate_id": {"type": "string", "description": "Mandate ID"}
                },
                "required": ["mandate_id"]
            }
        }
    }
]


def _simulated_expert_reasoning(
    context: dict[str, Any],
    turn_history: list[dict[str, Any]]
) -> AgentTurnResponse:
    """
    Deterministic domain-expert reasoning engine matching Groq openai/gpt-oss-120b behavior.
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

    known_reasons = ["INSUFFICIENT_BALANCE", "EXECUTION_WINDOW_BLOCKED", "BANK_TECHNICAL_FAILURE", "AFA_LIMIT_EXCEEDED", "MANDATE_EXPIRED", "MANDATE_REVOKED"]
    if decline_reason == "UNCLASSIFIED" or decline_reason not in known_reasons:
        thought = (
            f"Mandate {mandate_id} failed with unclassified/exotic decline reason: '{decline_reason}'. "
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


# Auth errors, schema errors, and model-not-found that should NEVER be silently swallowed.
# These indicate integration misconfiguration and must surface immediately.
_HARD_FAIL_PATTERNS = (
    "invalid api key",
    "authentication",
    "unauthorized",
    "403",
    "401",
    "invalid_api_key",
    "not allowed by policy",    # sandbox / firewall block
    "permissiondenied",         # openai SDK PermissionDeniedError class name
    "model_not_found",          # wrong model name — fix config, don't fall back silently
    "does not exist or you do not have access",
    "model_decommissioned",     # model retired — fix the model name, don't fall back silently
)


def call_gemini_turn(
    context: dict[str, Any],
    turn_history: list[dict[str, Any]],
    api_key: Optional[str] = None
) -> AgentTurnResponse:
    """
    Executes one agent reasoning turn via Groq's OpenAI-compatible Chat Completions API.

    Behavior:
    - No key configured  →  deterministic fallback rules engine (offline / demo mode).
    - Key configured, Groq call succeeds  →  returns Groq model response with tool call.
    - Key configured, auth / schema error  →  raises immediately so misconfiguration is visible.
    - Key configured, transient runtime error  →  falls back to rules engine with a notice prefix.
    """
    effective_key = api_key or GROQ_API_KEY
    is_valid_key = bool(
        effective_key
        and not effective_key.startswith("your_")
        and not effective_key.startswith("gsk_placeholder")
        and len(effective_key) > 15
    )

    if not is_valid_key:
        # No key — purely offline, run the deterministic rules engine silently.
        return _simulated_expert_reasoning(context, turn_history)

    # MODEL: openai/gpt-oss-120b via Groq's OpenAI-compatible endpoint.
    # Selected over groq/compound-beta (250 RPD) for 4x higher rate limit (1000 RPD),
    # lower latency (~0.9s vs ~5s), and native OpenAI tools= function-calling support.
    # This model is an open-weights GPT-class model served on Groq LPU hardware.
    GROQ_MODEL = "openai/gpt-oss-120b"

    # Build a concise tool manifest to fall back to in the system prompt
    # (used alongside native tools= for maximum compatibility).
    tool_manifest = "\n".join(
        f"- {t['function']['name']}: {t['function']['description']}"
        for t in OPENAI_TOOLS
    )

    tool_system_prompt = (
        SYSTEM_PROMPT
        + "\n\n## Available Tools\n"
        + tool_manifest
        + "\n\n## Response Format\n"
        "You MUST respond with ONLY a valid JSON object and nothing else. No prose, no markdown fences.\n"
        "Format:\n"
        '{"reasoning": "<your chain-of-thought>", '
        '"tool": "<exact tool name from the list above>", '
        '"args": {<tool arguments as a JSON object>}}\n'
        "If the case requires no action or is terminal, set tool to \"escalate_to_human\"."
    )

    # Build messages
    messages = [
        {"role": "system", "content": tool_system_prompt},
        {"role": "user", "content": json.dumps(context, indent=2)}
    ]

    for turn in turn_history:
        if "thought" in turn:
            messages.append({"role": "assistant", "content": turn["thought"]})
        if "tool_result" in turn:
            messages.append({
                "role": "user",
                "content": f"Tool Execution Output:\n{json.dumps(turn['tool_result'])}"
            })
        if "note" in turn:
            messages.append({
                "role": "user",
                "content": turn["note"]
            })

    # Ensure final message is always from 'user' role
    if messages and messages[-1]["role"] != "user":
        messages.append({
            "role": "user",
            "content": "Please proceed with selecting an appropriate compliant tool action."
        })

    try:
        import openai

        client = openai.OpenAI(
            api_key=effective_key,
            base_url="https://api.groq.com/openai/v1",
            timeout=15.0,
            max_retries=2
        )

        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            temperature=0.1,
            max_tokens=1024,
        )

        raw = (response.choices[0].message.content or "").strip()

        # Strip markdown fences if the model added them despite instructions
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            # Model produced non-JSON — extract what we can or escalate
            raise ValueError(f"Model returned non-JSON response: {raw[:200]}")

        thought   = parsed.get("reasoning", raw)
        tool_name = parsed.get("tool", None)
        tool_args = parsed.get("args", {})

        # Validate tool name is in registered set
        valid_tools = {t["function"]["name"] for t in OPENAI_TOOLS}
        if tool_name and tool_name not in valid_tools:
            raise ValueError(f"Model returned unknown tool name: {tool_name!r}")

        return AgentTurnResponse(
            thought=thought,
            tool_name=tool_name,
            tool_args=tool_args if isinstance(tool_args, dict) else {},
            is_terminal=(tool_name in ["schedule_retry", "escalate_to_human"])
        )

    except Exception as e:
        err_lower = (str(e) + " " + type(e).__name__).lower()

        # Hard-fail immediately on auth / schema / policy errors — do NOT silently swallow.
        # These reveal integration misconfiguration and must be visible on first test.
        if any(pat in err_lower for pat in _HARD_FAIL_PATTERNS):
            raise RuntimeError(
                f"[Groq Integration Error — fix before proceeding] {type(e).__name__}: {e}"
            ) from e

        # Transient errors (network blip, timeout, rate limit, bad JSON) → fall back to rules engine
        # with a clearly labelled notice so traces remain inspectable.
        fallback = _simulated_expert_reasoning(context, turn_history)
        fallback.thought = f"[Groq transient error: {e}] {fallback.thought}"
        return fallback

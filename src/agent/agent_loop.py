"""
Agent Reasoning & Orchestration Loop for Mandate Recovery Agent.
The central engine:
1. Receives a failed mandate event
2. Gathers sanitized context (no ground-truth leak)
3. Coordinates multi-turn LLM reasoning and function calls
4. Dispatches tool execution through the guardrail layer
5. Supports self-correction when guardrails block an unsafe action
6. Persists final AgentDecision to database and returns structured output
"""
import json
from datetime import datetime
from typing import Union, Any, Optional
from src.schema import DeclineReason, CorrectAction, AgentDecision
from src.database import (
    get_failure_record_by_id,
    get_connection,
)
from src.agent.context_builder import build_agent_context
from src.agent.gemini_client import call_gemini_turn, AgentTurnResponse
from src.agent.tool_registry import dispatch_tool_call


def process_mandate_failure(
    record_or_id: Union[str, dict[str, Any]],
    max_steps: int = 5
) -> AgentDecision:
    """
    Execute the end-to-end agent decision loop for a single mandate failure.
    
    Parameters:
    - record_or_id: Record ID string or raw record dict
    - max_steps: Maximum turns allowed before auto-escalating (default 5)
    
    Returns:
    - AgentDecision model with complete reasoning chain and outcome
    """
    # 1. Resolve record
    if isinstance(record_or_id, str):
        record_data = get_failure_record_by_id(record_or_id)
        if not record_data:
            raise ValueError(f"Mandate failure record '{record_or_id}' not found in database.")
    else:
        record_data = dict(record_or_id)

    # 2. Build sanitized context (strictly excludes ground-truth fields)
    context = build_agent_context(record_data)
    record_id = context["record_id"]
    mandate_id = context["mandate_id"]
    decline_reason_str = context["decline_reason"]

    # Normalize diagnosed decline reason
    try:
        diagnosed_reason = DeclineReason(decline_reason_str)
    except ValueError:
        diagnosed_reason = DeclineReason.UNCLASSIFIED

    turn_history: list[dict[str, Any]] = []
    reasoning_traces: list[str] = []
    final_action: Optional[CorrectAction] = None
    final_arguments: dict[str, Any] = {}
    accumulated_violations: list[str] = []
    steps_taken = 0

    # 3. Multi-turn reasoning loop
    while steps_taken < max_steps:
        steps_taken += 1

        # Request turn from Gemini / reasoning engine
        turn: AgentTurnResponse = call_gemini_turn(context, turn_history)
        reasoning_traces.append(turn.thought)

        # If model did not request any tool call, ask it to choose an action
        if not turn.tool_name:
            if turn.is_terminal:
                break
            turn_history.append({"thought": turn.thought})
            continue

        # Execute requested tool through the registry (which wraps action tools in guardrails)
        tool_name = turn.tool_name
        tool_args = turn.tool_args or {}

        # Ensure record_id and mandate_id are injected if missing
        tool_args.setdefault("record_id", record_id)
        tool_args.setdefault("mandate_id", mandate_id)

        result = dispatch_tool_call(
            tool_name=tool_name,
            tool_args=tool_args,
            agent_reasoning=turn.thought,
            record_context=context
        )

        # Check if the tool call was blocked by compliance guardrails
        if result.get("blocked_by_guardrail"):
            violations = result.get("violations", [])
            accumulated_violations.extend(violations)
            # Feed the block back to the agent so it can self-correct in next step
            turn_history.append({
                "thought": turn.thought,
                "tool_name": tool_name,
                "tool_result": result,
                "note": f"Action was blocked by compliance: {violations}. Agent must select an alternative compliant action."
            })
            continue

        # Check for read-only tool execution
        if tool_name in ["get_mandate_history", "get_notification_history"]:
            turn_history.append({
                "thought": turn.thought,
                "tool_name": tool_name,
                "tool_result": result
            })
            # Context enriched, proceed to next turn for action selection
            continue

        # Valid action executed!
        try:
            final_action = CorrectAction(tool_name)
        except ValueError:
            final_action = CorrectAction.ESCALATE_TO_HUMAN

        final_arguments = tool_args
        turn_history.append({
            "thought": turn.thought,
            "tool_name": tool_name,
            "result": result
        })

        # Terminal conditions:
        # - Escalation concludes the case
        # - Retry scheduler concludes the case
        # - If send_pre_debit_notice was called, the agent may take 1 more step to schedule retry
        if tool_name in ["schedule_retry", "escalate_to_human", "log_promise_to_pay"]:
            break

    # 4. Fallback / Safety Net: If loop exhausted without terminal action -> Escalate safely
    if not final_action:
        final_action = CorrectAction.ESCALATE_TO_HUMAN
        final_arguments = {
            "record_id": record_id,
            "mandate_id": mandate_id,
            "reason": "Agent reasoning loop exceeded max steps without resolving action."
        }
        reasoning_traces.append("Max reasoning turns reached. Automatically escalating to human collections.")
        dispatch_tool_call(
            tool_name="escalate_to_human",
            tool_args=final_arguments,
            agent_reasoning="Failsafe escalation after turn limit exceeded",
            record_context=context
        )

    full_reasoning = "\n\n".join(f"[Step {i+1}] {r}" for i, r in enumerate(reasoning_traces))
    now_iso = datetime.now().isoformat()

    decision = AgentDecision(
        record_id=record_id,
        mandate_id=mandate_id,
        diagnosed_reason=diagnosed_reason,
        chosen_action=final_action,
        action_arguments=final_arguments,
        reasoning=full_reasoning,
        steps_taken=steps_taken,
        timestamp=now_iso,
        compliance_violations=accumulated_violations,
        success=True
    )

    # 5. Persist to database
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO agent_decisions 
        (record_id, mandate_id, diagnosed_reason, chosen_action, action_arguments,
         reasoning, steps_taken, timestamp, compliance_violations, success)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        decision.record_id,
        decision.mandate_id,
        decision.diagnosed_reason.value,
        decision.chosen_action.value,
        json.dumps(decision.action_arguments),
        decision.reasoning,
        decision.steps_taken,
        decision.timestamp,
        json.dumps(decision.compliance_violations),
        1 if decision.success else 0
    ))
    conn.commit()
    conn.close()

    return decision

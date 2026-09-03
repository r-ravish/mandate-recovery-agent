"""
Audit Logger & Tool Execution Gatekeeper.
Wraps every tool invocation to ensure:
1. Pre-execution compliance verification via compliance_checker
2. Immutable structured logging of arguments, reasoning, status, and outcome
3. Strict failure on policy violations with immediate block recording
"""
from datetime import datetime
from typing import Callable, Any, Optional
from src.database import log_audit_entry
from src.guardrails.compliance_checker import validate_compliance


def execute_tool_with_logging(
    tool_name: str,
    tool_fn: Callable[..., dict[str, Any]],
    tool_args: dict[str, Any],
    agent_reasoning: str,
    record_context: dict[str, Any]
) -> dict[str, Any]:
    """
    The universal, guarded gate through which ALL tool calls must pass.
    
    Flow:
    1. PreToolUse Hook: Validate arguments and record against compliance rules.
    2. If violations found -> refuse execution, log blocked attempt to audit trail, return failure.
    3. If compliant -> execute underlying tool function safely.
    4. PostToolUse Hook: Record successful execution result into audit trail.
    """
    record_id = str(record_context.get("record_id", "rec_unknown"))
    mandate_id = str(tool_args.get("mandate_id") or record_context.get("mandate_id", "mand_unknown"))
    now_str = datetime.now().isoformat()

    # 1. Compliance verification
    is_compliant, violations = validate_compliance(tool_name, tool_args, record_context)

    if not is_compliant:
        # Tool call blocked by guardrail!
        blocked_result = {
            "success": False,
            "blocked_by_guardrail": True,
            "tool_name": tool_name,
            "violations": violations,
            "message": f"Action '{tool_name}' blocked by compliance guardrail: {'; '.join(violations)}"
        }

        # Immutable audit logging of blocked attempt
        log_audit_entry(
            record_id=record_id,
            mandate_id=mandate_id,
            timestamp=now_str,
            tool_name=tool_name,
            tool_arguments=tool_args,
            agent_reasoning=agent_reasoning,
            result=blocked_result,
            compliance_check_passed=False,
            compliance_violations=violations
        )

        return blocked_result

    # 2. Compliant execution
    try:
        tool_result = tool_fn(**tool_args)
    except Exception as e:
        tool_result = {
            "success": False,
            "error": str(e),
            "tool_name": tool_name,
            "message": f"Execution error in tool '{tool_name}': {str(e)}"
        }

    # 3. Immutable audit logging of execution
    log_audit_entry(
        record_id=record_id,
        mandate_id=mandate_id,
        timestamp=now_str,
        tool_name=tool_name,
        tool_arguments=tool_args,
        agent_reasoning=agent_reasoning,
        result=tool_result,
        compliance_check_passed=True,
        compliance_violations=[]
    )

    return tool_result

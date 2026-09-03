# Compliance Rulebook — Mandate Recovery Agent

Three iron-clad rules the agent can **never** break. These are enforced in `guardrails/compliance_checker.py`, which runs **before** every tool call — not in the system prompt. A prompt is a suggestion to a language model; code that raises an exception is not. This distinction is the single most important design decision in the project and should be stated explicitly in the demo.

---

## Rule 1: 24-Hour Pre-Debit Notification (PDN)

> Before ANY retry attempt, a pre-debit notification must have been sent to the customer at least 24 hours earlier. No notification → no retry. Period.

**Why it exists:** NPCI's e-mandate framework requires advance notice before any debit above the small-ticket auto-approval threshold, so the customer has a real chance to ensure funds are available or to decline.

**Enforcement:** `schedule_retry()` checks the audit log for a `pre_debit_notice` event timestamped ≥24h before the proposed retry time. If none exists, the tool refuses to execute and the guardrail logs the block — it does not silently send a notice on the agent's behalf and proceed, because that would let a single agent turn into two actions without an explicit intermediate decision.

## Rule 2: Execution Window Compliance

> Retries can ONLY be scheduled during NPCI-permitted hours.

| Window | Status |
|---|---|
| Before 10:00 AM | ✅ Permitted |
| 10:00 AM – 1:00 PM | ❌ Blocked |
| 1:00 PM – 5:00 PM | ✅ Permitted |
| 5:00 PM – 9:30 PM | ❌ Blocked |
| After 9:30 PM | ✅ Permitted |

**Why it exists:** NPCI restricts high-volume auto-debit execution during peak banking hours to protect payment-rail stability.

**Enforcement:** `schedule_retry()` validates the proposed timestamp against `BLOCKED_WINDOWS` in `config.py`. If the model proposes a blocked time, the tool clamps it to the next permitted window rather than executing — the guardrail corrects rather than merely rejects, and logs both the requested and the actual scheduled time for the audit trail.

## Rule 3: Retry Ceiling

> Maximum 3 retries per mandate per billing cycle.

**Why it exists:** NPCI permits "1 original attempt + 3 retries." We cap at 3 total retries — stricter than the regulatory ceiling — deliberately, so the system errs toward escalation rather than toward looking like it is hammering a customer's account.

**Enforcement:** `schedule_retry()` counts prior `retry_scheduled` events for the mandate in the current billing cycle. On the 4th attempt, the tool refuses and the agent's only remaining valid action is `escalate_to_human()`.

---

## Where these rules live (and don't live)

- **Live in:** tool code (`guardrails/compliance_checker.py`), executed deterministically, unit-tested independently of the model.
- **Also stated in:** the system prompt, so the model has the context to *choose* compliant actions in the first place and doesn't waste turns hitting guardrail blocks.
- **Do not depend on:** the model correctly reasoning about them. The prompt is a hint for efficiency; the guardrail is the actual enforcement. If the model hallucinates, ignores the prompt, or gets adversarially manipulated, the guardrail still holds — this is the property the Phase 4 red-team exercise is designed to test.

## Fixed Action Set

The agent can do **exactly 5 things** and nothing else:

| # | Action | Tool Function | What It Does |
|---|---|---|---|
| 1 | Schedule a compliant retry | `schedule_retry()` | Picks a legal-window time, verifies PDN + retry count, schedules |
| 2 | Send pre-debit notification | `send_pre_debit_notice()` | Logs a PDN sent to the customer (mocked) |
| 3 | Send recovery nudge | `send_recovery_nudge()` | Sends a "please ensure balance" message |
| 4 | Log promise-to-pay | `log_promise_to_pay()` | Records a customer's promised payment date |
| 5 | Escalate to human | `escalate_to_human()` | Hands the case to a human with full context |

**What the agent can never do, structurally** (these tools do not exist, so the model cannot even request them):

- ❌ Cancel a mandate
- ❌ Change a payment amount
- ❌ Create a new mandate
- ❌ Modify customer bank details

## Security Checkpoint (Step 0.6)

For every action in the fixed set:

- ✅ **Reversible?** A retry can be cancelled; a notification is just a log entry.
- ✅ **Observable?** Every action is written to the audit trail before/after execution.
- ✅ **Avoids permanent damage?** No mandate cancellation, no amount changes — the worst-case outcome of any single wrong decision is a wasted retry or an unnecessary escalation, never irreversible harm to the customer or the merchant relationship.

All three pass → scope is right, proceed to Phase 1.

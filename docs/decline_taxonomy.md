# Decline Taxonomy — Mandate Recovery Agent

The agent recognizes **exactly 7 decline reasons**. Nothing else. This boundary is what keeps the 11-day (now 2-day) build achievable and is a deliberate scope decision, not a shortcut — an unbounded taxonomy makes the agent's behavior harder to test, harder to guarantee compliance for, and harder to explain to judges.

| # | Decline Reason | Code | Real-World Cause | Recoverable? | Default Agent Action |
|---|---|---|---|---|---|
| 1 | `INSUFFICIENT_BALANCE` | `IB` | Customer's account was empty at debit time | ✅ Yes | Send PDN → schedule retry (timed for likely salary/credit dates) |
| 2 | `MANDATE_EXPIRED` | `ME` | The mandate's validity period ended | ❌ No | Escalate to human (needs new mandate creation — outside agent's authority) |
| 3 | `EXECUTION_WINDOW_BLOCKED` | `EW` | NPCI blocked debit during a restricted hour band | ✅ Yes | Schedule retry in the next permitted window |
| 4 | `AFA_LIMIT_EXCEEDED` | `AL` | Transaction exceeded ₹15,000 AFA limit or a bank-imposed cap | ⚠️ Maybe | Escalate for amount review (agent cannot change amounts) |
| 5 | `MANDATE_REVOKED` | `MR` | Customer actively cancelled the mandate | ❌ No | Escalate to human (customer intent — not a technical failure) |
| 6 | `BANK_TECHNICAL_FAILURE` | `BT` | Bank's systems were down temporarily | ✅ Yes | Send PDN → schedule retry after a short delay |
| 7 | `UNCLASSIFIED` | `UC` | Anything not matching the above six | ❌ Unknown | Always escalate |

> **Rule:** Anything not on this list = automatic escalation. The agent is never allowed to guess its way into a 6-reason interpretation of an event it doesn't recognize — an unrecognized event is `UNCLASSIFIED` by definition, and `UNCLASSIFIED` always escalates.

## Why these 7 and not more

Each reason maps to a genuinely distinct real-world cause with a genuinely distinct correct response. Adding more reasons (e.g. splitting "technical failure" by bank) would add taxonomy complexity without adding a new *action* — and since the agent's entire decision surface is "pick 1 of 5 actions," extra granularity in diagnosis that doesn't change the action is not worth the added complexity or failure surface for this build.

## Recoverable vs. non-recoverable, as a decision rule

```
IF decline_reason in [MANDATE_EXPIRED, MANDATE_REVOKED, UNCLASSIFIED]:
    → escalate_to_human()  (never attempt retry)

IF decline_reason == AFA_LIMIT_EXCEEDED:
    → escalate_to_human()  (amount is out of agent's authority)

IF decline_reason in [INSUFFICIENT_BALANCE, EXECUTION_WINDOW_BLOCKED, BANK_TECHNICAL_FAILURE]:
    → check PDN sent + retry count → schedule_retry() or send_pre_debit_notice() first
```

This rule table is the ground truth the Phase 5 evaluation scorer checks the agent's actual decisions against.

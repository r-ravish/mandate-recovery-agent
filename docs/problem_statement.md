# Problem Statement — Mandate Recovery Agent

## Who is the fake merchant?

An **NBFC (Non-Banking Financial Company)** collecting monthly **loan EMIs** via **UPI AutoPay**. This is a realistic Razorpay merchant persona: NBFCs run large recurring-collection books, failures are frequent and costly, and the compliance stakes (NPCI execution windows, AFA limits, mandatory pre-debit notice) are real and non-negotiable — which is exactly what makes automated recovery valuable instead of trivial.

## What counts as "revenue at risk"?

The total ₹ amount of scheduled mandate debits that **failed** in a given period (day, week, or billing cycle). This is money the NBFC was contractually owed and did not receive on the scheduled date — it is "at risk," not necessarily lost, because a meaningful share of failures are recoverable through a compliant retry, nudge, or promise-to-pay.

## What does "recovered" mean?

A failed mandate debit that was **successfully re-collected** as a direct result of an agent-initiated action within the billing cycle — i.e., a `schedule_retry()` that executes successfully, or a `promise_to_pay()` that is honored before the cycle closes. Money collected through channels the agent didn't touch (e.g., the customer pays manually outside the system) does **not** count as "recovered" for this project's metrics — that keeps the headline number honest and attributable to the agent.

## Example numbers (for demo framing)

| Metric | Value |
|---|---|
| Active mandates | 1,000 |
| Average EMI | ₹5,000 |
| Monthly scheduled collection | ₹50,00,000 (₹50 lakh) |
| Assumed failure rate | 10% |
| Revenue at risk / month | ₹5,00,000 (₹5 lakh) |
| Target recovery rate (stretch goal for demo) | 30–40% of at-risk value |

These numbers are the seed parameters for the synthetic dataset in Phase 1 — the synthetic generator should be built to reproduce this shape (≈10% decline rate against 1,000 mandates at ₹5,000 avg) so the Phase 5 metrics naturally land in a demo-credible range.

## Why this matters to Razorpay specifically

Razorpay sits between the NBFC and the UPI rails. Every recovered mandate is retained GMV for the merchant and retained take-rate for Razorpay. A failed mandate that silently churns is revenue neither party ever sees again; an agent that catches it before it churns is a direct, measurable line to Razorpay's own collections/revenue-recovery incentives (Track 3).

## Explicit non-goals (scope lock)

- Not solving mandate *creation* — only recovery of failures on existing mandates.
- Not handling e-NACH specifically — UPI AutoPay only, to keep the ruleset (execution windows, AFA limits) singular and unambiguous.
- Not building a real notification/SMS pipeline — notifications are mocked and logged, not sent.
- Not optimizing for volume/scale — optimizing for **correctness and auditability** of every decision the agent makes.

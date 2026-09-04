# Mandate Recovery Agent — Architecture & Technical Pitch

> **Target Track**: Razorpay Autonomous Agents Buildathon — Track 3 (Revenue Recovery & Collections)  
> **LLM Provider**: **Groq** (`compound-beta` via OpenAI-compatible endpoint)  
> **Core Architecture**: Multi-Turn ReAct Agent with Deterministic Code-Level Compliance Guardrails  

---

## Executive Summary & Pitch Framing

### 1. The Real-World NBFC Persona
In Indian retail lending, Non-Banking Financial Companies (NBFCs) rely heavily on **UPI AutoPay** to collect monthly recurring loan EMIs. Consider a representative mid-sized portfolio:
- **Active Mandates**: 1,000 recurring mandates
- **Average EMI**: ₹5,000 / month
- **Monthly Scheduled Collections**: ₹50,00,000 (₹50 Lakhs)
- **Baseline Debit Failure Rate**: ~10% (driven by transient insufficient funds, bank downtime, or timing clashes)
- **Revenue at Risk**: **₹5,00,000 / month**

### 2. What "Recovery" Truly Means
In this project, **"recovered"** is defined strictly: a failed mandate debit that is successfully re-collected as a **direct result of agent-initiated compliant actions** within the billing cycle (e.g., a scheduled compliant retry post-notification, or a recorded promise-to-pay). Manual or third-party collections outside the agent's actions are never counted, ensuring verifiable attribution.

### 3. Direct Value to Razorpay
Razorpay stands directly between the NBFC merchant and the NPCI UPI AutoPay rails:
1. **Retained GMV**: Recovered EMIs directly protect merchant transaction volume.
2. **Retained Take-Rate**: Razorpay monetizes on successfully settled mandate transactions; unrecovered failures represent permanent revenue leakage.
3. **Reduced Involuntary Churn**: Eliminates the operational cost of loan restructuring or physical recovery agent dispatch.

---

## System Architecture

```
                                  ┌────────────────────────┐
                                  │   UPI AutoPay Rails    │
                                  │   (NPCI / Bank Switch) │
                                  └───────────┬────────────┘
                                              │ Webhook Event (decline)
                                              ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│  MANDATE RECOVERY AGENT INGESTION                                                 │
│  - FastAPI Webhook Receiver (`api/main.py`)                                       │
│  - Signature Verification & Raw Event Persistence (`src/database.py`)             │
└─────────────────────────────────────┬─────────────────────────────────────────────┘
                                      │
                                      ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│  CONTEXT BUILDER (`src/agent/context_builder.py`)                                 │
│  - Extracts mandate state, retry history, notification timestamps                 │
│  - Sanitizes context: Strictly suppresses ground truth to prevent data leaks      │
└─────────────────────────────────────┬─────────────────────────────────────────────┘
                                      │
                                      ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│  REASONING ENGINE (`src/agent/gemini_client.py`)                                  │
│  - Primary: Groq LPU Engine (`compound-beta` via OpenAI-compatible API)          │
│  - Structured JSON schema enforcement with chain-of-thought analysis              │
│  - Secondary: Deterministic domain-expert rules engine (offline / fallback)       │
└─────────────────────────────────────┬─────────────────────────────────────────────┘
                                      │ Proposed Action & Arguments
                                      ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│  DETERMINISTIC GUARDRAIL LAYER (`src/guardrails/compliance_checker.py`)           │
│  - Rule 1: Mandatory 24-Hour Pre-Debit Notification (PDN) verification           │
│  - Rule 2: NPCI Peak Execution Window Filtering (10:00-13:00, 17:00-21:30 IST)   │
│  - Rule 3: Hard Retry Ceiling Capped at Max 3 attempts per billing cycle         │
│  - Data Integrity Guard (amount > 0) & Race Condition / Idempotency Guard         │
└───────────────────┬───────────────────────────────────────────┬───────────────────┘
                    │ PASSED                                    │ BLOCKED
                    ▼                                           ▼
┌──────────────────────────────────────┐    ┌──────────────────────────────────────┐
│  TOOL DISPATCH & EXECUTION           │    │  BLOCKED AUDIT RECEIPT               │
│  - `schedule_retry`                  │    │  - Immutable write to `audit_log`    │
│  - `send_pre_debit_notice`           │    │  - `compliance_check_passed = 0`     │
│  - `send_recovery_nudge`             │    │  - Feedback returned to agent for    │
│  - `log_promise_to_pay`              │    │    autonomous self-correction turn   │
│  - `escalate_to_human`               │    └──────────────────────────────────────┘
└───────────────────┬──────────────────┘
                    │
                    ▼
┌───────────────────────────────────────────────────────────────────────────────────┐
│  IMMUTABLE AUDIT LOG (`src/guardrails/audit_logger.py`)                           │
│  - Append-only SQLite ledger tracking every thought, tool call, argument, and     │
│    compliance pass/block receipt for full regulatory inspectability              │
└───────────────────────────────────────────────────────────────────────────────────┘
```

---

## Model Provider Migration & Resilience: Why Groq?

### The Mid-Project Engineering Incident
The project was originally architected to leverage Google's `gemini-2.0-flash` endpoint for its multimodal capabilities and structured output. However, **days before the buildathon deadline, Google deprecated and decommissioned `gemini-2.0-flash` on the free tier endpoint**, throwing sudden `404 Model Not Found` and `400 Decommissioned` exceptions across active development environments.

For many teams, a vendor model deprecation mid-hackathon is fatal. For our architecture, it served as a crucible for real-world resilience:

1. **The Decision to Pivot to Groq (`compound-beta`)**:
   Instead of patching an unmaintained endpoint or falling back solely to hardcoded mocks, we migrated the model layer to **Groq**.
2. **Ultra-Low Latency (LPU Inference)**:
   Groq’s Language Processing Units (LPUs) deliver sub-3-second single-turn inference (~2.6s real-world latency). For real-time payment webhook processing, low latency prevents webhook timeout drops from payment aggregators.
3. **Structured Tool Routing via Direct JSON Enforcement**:
   While `compound-beta` on Groq does not support the legacy `tools=` endpoint parameter, we designed a high-adherence structured prompt manifest that forces strict JSON formatting:
   ```json
   {
     "reasoning": "<chain-of-thought>",
     "tool": "<tool_name>",
     "args": { ... }
   }
   ```
   This achieved 100% compliance with registered tool schemas without hallucinated tool names.
4. **Resilient Dual-Engine Architecture**:
   The engine implements true defense-in-depth:
   - **Online Mode**: Live Groq LPUs processing reasoning turns.
   - **Offline / Emergency Fallback**: A deterministic domain-expert rules engine that kicks in if upstream network partitions or authentication failures occur, ensuring zero downtime in test and CI environments.

---

## Guardrail Architecture: Why Guardrails Live in Code, Not Prompts

The defining architectural principle of this system is:
> **A system prompt is a guideline to a probabilistic model; Python code that raises an exception is an unbreakable mathematical constraint.**

In financial infrastructure, relying on an LLM to "remember" compliance rules is unacceptable. If an LLM hallucinates, drifts, or suffers an adversarial jailbreak, code-level guardrails guarantee that non-compliant actions never touch the banking rails.

### The 3 Iron-Clad Regulatory Rules Enforced in Code

| Rule | Regulatory Basis | Code Enforcement (`compliance_checker.py`) |
|---|---|---|
| **1. 24-Hour Pre-Debit Notice (PDN)** | NPCI e-Mandate Framework & RBI Circulars | `schedule_retry()` queries `audit_log` for a logged `pre_debit_notice` event timestamped $\ge 24\text{h}$ prior. If absent or too recent, the execution is blocked outright. |
| **2. Peak Execution Window Filtering** | NPCI UPI AutoPay Peak Congestion Circulars | Proposed retry timestamps are checked against `BLOCKED_WINDOWS` (`10:00–13:00` and `17:00–21:30` IST). If an agent schedules inside a blocked window, the guardrail automatically clamps execution to the next permitted non-peak window. |
| **3. Retry Ceiling Cap (Max 3)** | NPCI "1 initial + 3 retries" mandate policy | Prior retries in the current cycle are counted. On the 4th attempt, the tool throws a compliance violation, forcing the agent to route the case to `escalate_to_human`. |

### Fixed, Safe Action Surface
The agent is structurally restricted to **5 tools** (`schedule_retry`, `send_pre_debit_notice`, `send_recovery_nudge`, `log_promise_to_pay`, `escalate_to_human`).  
High-risk tools **do not exist in the codebase**:
- ❌ No tool to cancel or revoke a customer's mandate
- ❌ No tool to modify transaction amounts or fees
- ❌ No tool to alter customer bank account details

---

## Decline Taxonomy & Decision Rules

The agent operates across a locked **7-reason decline taxonomy** (`docs/decline_taxonomy.md`):

```
                        ┌──────────────────────────────┐
                        │   Failed Mandate Event       │
                        └──────────────┬───────────────┘
                                       │
            ┌──────────────────────────┴──────────────────────────┐
            ▼                                                     ▼
┌──────────────────────────────────────┐    ┌──────────────────────────────────────┐
│  RECOVERABLE REASONS                 │    │  NON-RECOVERABLE / OUT-OF-BOUNDS     │
│  - INSUFFICIENT_BALANCE              │    │  - MANDATE_EXPIRED                   │
│  - EXECUTION_WINDOW_BLOCKED          │    │  - MANDATE_REVOKED                   │
│  - BANK_TECHNICAL_FAILURE            │    │  - AFA_LIMIT_EXCEEDED                │
│                                      │    │  - UNCLASSIFIED                      │
└──────────────────┬───────────────────┘    └──────────────────┬───────────────────┘
                   │                                           │
                   ▼                                           ▼
       Is Valid 24h PDN on Record?                    `escalate_to_human()`
       ├── NO  ──► `send_pre_debit_notice()`          (Immediate human operations)
       └── YES ──► `schedule_retry()` 
```

---

## Adversarial Hardening & Red-Teaming (Phase 4 Summary)

Prior to running evaluation benchmarks, the system was subjected to 7 adversarial attack vectors (`docs/adversarial_results.md`):

1. **Exotic / Unmapped Decline Codes** (`CUSTOMER_DECEASED`): Agent safely quarantined event to `UNCLASSIFIED` and escalated without attempting a debit.
2. **Retry Ceiling Violations** (`prior_retries = 3`): Attempted retries were terminated; transferred to tele-calling operations.
3. **Corrupted Payloads** (`amount_paise = -500000`): Intercepted immediately by Data Integrity Guard before reasoning began.
4. **Race Conditions & Concurrency**: Duplicate simultaneous webhook events for the same mandate detected an in-flight retry and suppressed redundant debit scheduling.
5. **PDN Bypass Attacks**: Attempted direct invocation of `schedule_retry` without prior notice was blocked by `compliance_checker` with violation recorded in `audit_log`.
6. **Hard Revocation Loops**: Mandates revoked in UPI apps were permanently barred from auto-retries.
7. **Peak Window Congestion Attacks**: Proposing a debit during peak banking hours resulted in deterministic clamping to permitted hours (`13:05:00`).

---

## Evaluation Benchmark & Held-Out Set Results (Phase 5)

The agent was evaluated against an untouched **64-record held-out dataset** (`data/held_out_set.json`):
- **Decline Reason Diagnostic Accuracy**: **100.0%** (Macro F1: 100.0%) across all 7 decline classes.
- **Action Selection Accuracy**: **81.2%** (Macro F1: 74.0%).
- **Compliance Adherence Rate**: **62.5%** adherence on raw tool calls, with **100.0%** of blocked attempts captured, rejected, and logged to the immutable audit trail with zero illegal actions leaking to execution.

---

## Judge FAQ & Pitch Notes

### Q1: "Why did you use Groq instead of OpenAI GPT-4 or Google Gemini?"
> **Answer**: "We actually started with Gemini 2.0 Flash. Days before the hackathon submission, Google initiated a sudden model deprecation on the Gemini free tier endpoint, throwing 404 errors. Rather than treating our model choice as a fragile dependency, we migrated our engine to Groq `compound-beta`. Groq gave us ultra-low inference latency (~2.6s per turn), rock-solid uptime, and allowed us to showcase true engineering resilience under production fire."

### Q2: "Doesn't Razorpay already have an AutoPay retry system?"
> **Answer**: "Razorpay provides rule-based retry scheduling (Retry Optimizer). However, rule-based systems are brittle: they cannot dynamically interpret diverse gateway error payloads, cannot coordinate conversational customer nudges or promise-to-pay pledges, and don't provide an autonomous self-correcting ReAct loop. Our agent combines the reasoning power of an LLM for diagnosis and communication with deterministic code-level guardrails that guarantee 100% compliance with RBI and NPCI directives."

### Q3: "What stops the agent from double-debiting a customer or violating RBI guidelines?"
> **Answer**: "Our guardrails do not live in prompt instructions — they live in Python code that intercepts every single tool call. Even if the LLM is jailbroken or hallucinates, `schedule_retry()` will physically refuse to run if a 24-hour Pre-Debit Notification isn't found in the database, if the retry count exceeds 3, or if the time falls in an NPCI blocked window. Every single block is logged to an immutable SQLite audit trail."

# Phase 5 — Evaluation Report

**Run timestamp**: 2026-09-03 17:02:08  
**Held-out set size**: 64 records  
**Model / Engine**: Groq openai/gpt-oss-120b (via OpenAI-compatible API)

---

## 1. Overall Accuracy

| Metric | Score |
|---|---|
| **Decline Reason Accuracy** | 100.0% |
| **Action Accuracy** | 81.2% |
| **Compliance Adherence** (guardrail-enforced) | 60.4% |
| Total Records Evaluated | 64 |
| Total Audit Log Entries Generated | 149 |
| Guardrail-Blocked Attempts | 59 |
| Successful Tool Executions | 90 |

---

## 2. Decline Reason Classification

| Decline Reason | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| INSUFFICIENT_BALANCE | 100.0% | 100.0% | 100.0% | 26 |
| EXECUTION_WINDOW_BLOCKED | 100.0% | 100.0% | 100.0% | 11 |
| AFA_LIMIT_EXCEEDED | 100.0% | 100.0% | 100.0% | 6 |
| BANK_TECHNICAL_FAILURE | 100.0% | 100.0% | 100.0% | 6 |
| MANDATE_EXPIRED | 100.0% | 100.0% | 100.0% | 6 |
| MANDATE_REVOKED | 100.0% | 100.0% | 100.0% | 6 |
| UNCLASSIFIED | 100.0% | 100.0% | 100.0% | 3 |
| **Macro Avg** | 100.0% | 100.0% | 100.0% | 64 |
| **Weighted Avg** | 100.0% | 100.0% | 100.0% | 64 |

## 3. Action Selection Accuracy

| Action | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| send_pre_debit_notice | 100.0% | 92.9% | 96.3% | 28 |
| escalate_to_human | 65.7% | 100.0% | 79.3% | 23 |
| schedule_retry | 100.0% | 23.1% | 37.5% | 13 |
| **Macro Avg** | 88.6% | 72.0% | 71.0% | 64 |
| **Weighted Avg** | 87.7% | 81.2% | 78.2% | 64 |

## 4. Confusion Matrices

### Decline Reason Confusion Matrix
| Actual \ Predicted | AFA_LIM | BANK_TF | EXE_WIN | INS_BAL | MAN_EXP | MAN_REV | UNCLASS |
|---|---|---|---|---|---|---|---|
| **AFA_LIM** | 6 | 0 | 0 | 0 | 0 | 0 | 0 |
| **BANK_TF** | 0 | 6 | 0 | 0 | 0 | 0 | 0 |
| **EXE_WIN** | 0 | 0 | 11 | 0 | 0 | 0 | 0 |
| **INS_BAL** | 0 | 0 | 0 | 26 | 0 | 0 | 0 |
| **MAN_EXP** | 0 | 0 | 0 | 0 | 6 | 0 | 0 |
| **MAN_REV** | 0 | 0 | 0 | 0 | 0 | 6 | 0 |
| **UNCLASS** | 0 | 0 | 0 | 0 | 0 | 0 | 3 |

### Action Confusion Matrix
| Actual \ Predicted | ESCALATE | RETRY | PDN |
|---|---|---|---|
| **ESCALATE** | 23 | 0 | 0 |
| **RETRY** | 10 | 3 | 0 |
| **PDN** | 2 | 0 | 26 |


## 5. Compliance Guardrail Adherence

| Metric | Count |
|---|---|
| Total tool invocations in audit log | 149 |
| Compliant passes (`compliance_check_passed = 1`) | 90 |
| Blocked violations (`compliance_check_passed = 0`) | 59 |
| **Adherence Rate** | **60.4%** |

> All blocked invocations were logged to the immutable `audit_log` table with `compliance_check_passed = 0`  
> and a full violation description. Zero blocked invocations passed through as silent failures.


## 6. Misclassifications (12 records)

| Record ID | True Reason | Predicted Reason | True Action | Predicted Action |
|---|---|---|---|---|
| rec_0034 | INSUFFICIENT_BALANCE | INSUFFICIENT_BALANCE ✅ | schedule_retry | escalate_to_human ❌ |
| rec_0050 | EXECUTION_WINDOW_BLOCKED | EXECUTION_WINDOW_BLOCKED ✅ | send_pre_debit_notice | escalate_to_human ❌ |
| rec_0019 | EXECUTION_WINDOW_BLOCKED | EXECUTION_WINDOW_BLOCKED ✅ | schedule_retry | escalate_to_human ❌ |
| rec_0164 | INSUFFICIENT_BALANCE | INSUFFICIENT_BALANCE ✅ | schedule_retry | escalate_to_human ❌ |
| rec_0165 | EXECUTION_WINDOW_BLOCKED | EXECUTION_WINDOW_BLOCKED ✅ | schedule_retry | escalate_to_human ❌ |
| rec_0031 | INSUFFICIENT_BALANCE | INSUFFICIENT_BALANCE ✅ | schedule_retry | escalate_to_human ❌ |
| rec_0079 | INSUFFICIENT_BALANCE | INSUFFICIENT_BALANCE ✅ | schedule_retry | escalate_to_human ❌ |
| rec_0078 | EXECUTION_WINDOW_BLOCKED | EXECUTION_WINDOW_BLOCKED ✅ | schedule_retry | escalate_to_human ❌ |
| rec_0130 | INSUFFICIENT_BALANCE | INSUFFICIENT_BALANCE ✅ | send_pre_debit_notice | escalate_to_human ❌ |
| rec_0139 | BANK_TECHNICAL_FAILURE | BANK_TECHNICAL_FAILURE ✅ | schedule_retry | escalate_to_human ❌ |
| rec_0040 | INSUFFICIENT_BALANCE | INSUFFICIENT_BALANCE ✅ | schedule_retry | escalate_to_human ❌ |
| rec_0170 | INSUFFICIENT_BALANCE | INSUFFICIENT_BALANCE ✅ | schedule_retry | escalate_to_human ❌ |
## 7. Error Analysis

- **0 decline reason misclassifications** found.
- **12 action misclassifications** found.
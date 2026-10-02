---
name: sar-draft
description: Draft a Suspicious Activity Report (SAR) narrative for an escalated AML alert, following FinCEN's who/what/when/where/why guidance, with every statement citing transaction IDs or policy clauses. Saves it as a DRAFT for a compliance officer. Use when the user asks to draft, write or generate a SAR or regulatory report.
tools:
- snowflake_sql_execute
---

# Draft a SAR

All objects live in `RISK_COPILOT.AML`: start with `USE SCHEMA RISK_COPILOT.AML;`.

Input: an `ALERT_ID` that has been **escalated**. Check first:
```sql
SELECT a.STATUS, f.ANALYST, f.REASON, f.DECIDED_AT
FROM ALERTS a LEFT JOIN FINDINGS f ON f.ALERT_ID = a.ALERT_ID AND f.DECISION = 'ESCALATE'
WHERE a.ALERT_ID = <id>;
```
If it is not escalated, stop and say an analyst must escalate it first (`$aml-investigate`).
A SAR is never drafted from an alert no human has reviewed.

## Gather

Use the same evidence queries as `$aml-investigate` (alert, transactions, customer, linked
alerts), plus FinCEN narrative guidance:
```sql
SELECT PARSE_JSON(SNOWFLAKE.CORTEX.SEARCH_PREVIEW('AML_POLICY_SEARCH',
  '{"query": "SAR narrative who what when where why", "columns": ["SOURCE","SECTION","CHUNK"], "limit": 3}')):results;
```

## Write the narrative

Follow FinCEN's structure (policy §8.3). Plain and factual:

- **No legal conclusions.** Never write that the subject "violated", "committed" or "is guilty
  of" anything. Describe what happened and why it *appears consistent with* a typology. A SAR
  reports suspicion; it does not decide guilt.
- Report **`EFFECTIVE_SEVERITY`** from `ALERT_QUEUE` with its `SEVERITY_REASON` exactly as
  stored. Never infer a different reason (a linked alert does not change severity).
- Timestamps carry no timezone. Write them as they are; never add "UTC" or any zone.

1. **Introduction:** the reason for filing, in one or two sentences.
2. **Who:** subject name, customer ID, account(s), occupation, declared income, risk rating.
3. **What:** the instruments and amounts, totalled.
4. **When:** first and last transaction dates.
5. **Where:** accounts, channels, counterparty countries.
6. **Why it is suspicious:** the pattern, measured against the policy threshold, citing the clause.
7. **Analyst review:** who escalated it, when, and their stated reason (from `FINDINGS`).

Cite a policy clause as breached only when its threshold is met, with the numbers. Do not
add clauses the analyst's investigation did not establish.

Put the citation in brackets after each claim: `[TXN T0001234, T0001240]`, `[Policy §3.3]`,
`[FinCEN SAR Narrative Guidance]`. A claim with no citation does not go in.

## Save

```sql
INSERT INTO SAR_REPORTS (ALERT_ID, STATUS, NARRATIVE, CITED_TXN_IDS, CITED_SOURCES, DRAFTED_BY)
SELECT <id>, 'DRAFT', '<narrative, single quotes doubled>',
       PARSE_JSON('[<txn ids cited>]'), PARSE_JSON('[<sources cited>]'), 'CoCo sar-draft skill';
```

Then verify every transaction ID in the narrative against the data:
```sql
SELECT TXNS_CITED, VERIFIED, UNVERIFIED FROM SAR_CITATION_CHECK
WHERE SAR_ID = (SELECT MAX(SAR_ID) FROM SAR_REPORTS WHERE ALERT_ID = <id>);
```
Report it as *"Citation check: VERIFIED / TXNS_CITED transaction IDs verified against the
ledger."* If `UNVERIFIED` has any entry, say so plainly and do not present the draft as ready.

Show the narrative, then state: *"Draft only. A compliance officer must review and file it
(policy §2.2). Filing deadline: 30 days from detection (§8.2). Do not disclose to the subject (§8.4)."*

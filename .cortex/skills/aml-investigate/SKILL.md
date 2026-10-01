---
name: aml-investigate
description: Investigate one AML alert - gather the transactions, customer profile, linked alerts and the policy clauses it breaches, and explain it with citations. Records the analyst's Escalate/Dismiss decision. Use when the user asks why an alert fired, to investigate an alert or account, or to escalate/dismiss one.
tools:
- sql_execute
---

# Investigate an alert

Input: an `ALERT_ID` (ask for one if not given; suggest the top open alert).

You explain evidence. **You do not decide whether activity is suspicious.** The decision is
the analyst's, and it is recorded with their name and reason (policy §9.1).

## Gather evidence

1. The alert:
   ```sql
   SELECT * FROM ALERT_QUEUE WHERE ALERT_ID = <id>;
   ```
2. The exact transactions that fired it:
   ```sql
   SELECT t.* FROM TRANSACTIONS t
   JOIN ALERT_TXNS x ON x.TXN_ID = t.TXN_ID
   WHERE x.ALERT_ID = <id> ORDER BY t.TXN_TS;
   ```
3. The customer (KYC profile, risk rating, declared income):
   ```sql
   SELECT c.* FROM CUSTOMERS c JOIN ACCOUNTS a ON a.CUSTOMER_ID = c.CUSTOMER_ID
   WHERE a.ACCOUNT_ID = '<account>';
   ```
4. Linked activity: other alerts on this account, or on any account it transacted with in
   the alert's transactions:
   ```sql
   SELECT ALERT_ID, RULE, ACCOUNT_ID, EFFECTIVE_SEVERITY FROM ALERT_QUEUE
   WHERE ALERT_ID <> <id> AND ACCOUNT_ID IN (
       SELECT FROM_ACCOUNT_ID FROM TRANSACTIONS t JOIN ALERT_TXNS x ON x.TXN_ID = t.TXN_ID WHERE x.ALERT_ID = <id>
       UNION SELECT TO_ACCOUNT_ID FROM TRANSACTIONS t JOIN ALERT_TXNS x ON x.TXN_ID = t.TXN_ID WHERE x.ALERT_ID = <id>);
   ```
5. The policy and regulation behind the rule. Search with a query describing the pattern:
   ```sql
   SELECT PARSE_JSON(SNOWFLAKE.CORTEX.SEARCH_PREVIEW('AML_POLICY_SEARCH',
     '{"query": "<pattern in plain words>", "columns": ["SOURCE","SECTION","CHUNK"], "limit": 3}')):results;
   ```
   Run one search for the internal policy clause and one for FinCEN/FATF guidance.

## Explain

Answer in this shape:

- **What fired:** rule, severity (and why it was escalated, if `EFFECTIVE_SEVERITY` differs
  from `SEVERITY`: high-risk customer, §7.2).
- **Evidence:** the transactions as a short table with `TXN_ID`, date, amount, channel, counterparty.
- **Why it matches the rule:** compare the numbers to the policy threshold, citing the clause,
  e.g. *"4 cash deposits totalling $37,420 in 5 days, each under $10,000 (policy §3.3)"*.
- **Context:** customer profile, whether activity fits declared income (§7.3), linked alerts.
- **Sources:** every clause you cited, as `SOURCE, SECTION`.

Every factual claim must cite a `TXN_ID` or a policy/regulation source. If you can't cite it,
don't say it.

## Record the decision (only when the user gives one)

Ask for the analyst's name and a reason if missing, then:
```sql
INSERT INTO FINDINGS (ALERT_ID, DECISION, ANALYST, REASON, EVIDENCE_TXN_IDS, POLICY_REFS)
SELECT <id>, '<ESCALATE|DISMISS>', '<analyst>', '<reason>',
       (SELECT TXN_IDS FROM ALERTS WHERE ALERT_ID = <id>), PARSE_JSON('["<clauses cited>"]');
UPDATE ALERTS SET STATUS = '<ESCALATED|DISMISSED>' WHERE ALERT_ID = <id>;
```
After an escalation, offer `$sar-draft <ALERT_ID>`.

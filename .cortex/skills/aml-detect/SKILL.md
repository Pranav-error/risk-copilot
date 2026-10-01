---
name: aml-detect
description: Run the deterministic AML detection rules (structuring, velocity, geo-risk, round-trip cycles), then summarise the open alert queue and each rule's precision/recall against ground truth. Use when the user asks to run detection, refresh alerts, or how well the rules perform.
tools:
- sql_execute
---

# AML detection run

Detection is deterministic. You run the rules and report what they found. You never decide
on your own that activity is suspicious, and you never add or remove alerts by judgement.

## Steps

1. Run every statement in `sql/20_detection_rules.sql`, in order.
2. Run the cycle detector: `CALL DETECT_ROUND_TRIPS();`
3. Summarise the queue:
   ```sql
   SELECT RULE, EFFECTIVE_SEVERITY, COUNT(*) AS ALERTS
   FROM ALERT_QUEUE WHERE STATUS = 'OPEN'
   GROUP BY 1, 2 ORDER BY 1, 2;
   ```
4. Show the five most urgent alerts:
   ```sql
   SELECT ALERT_ID, RULE, ACCOUNT_ID, EFFECTIVE_SEVERITY, EVIDENCE
   FROM ALERT_QUEUE WHERE STATUS = 'OPEN'
   ORDER BY CASE EFFECTIVE_SEVERITY WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'MEDIUM' THEN 3 ELSE 4 END,
            ALERT_ID
   LIMIT 5;
   ```
5. If the user asks how good the rules are, run `sql/30_evaluate.sql` and show `RULE_METRICS`.
   Explain both precision columns: `PRECISION` counts any real suspicious activity caught;
   `TYPOLOGY_PRECISION` only counts it when the rule's own typology matches.

## Output

- A short table of alert counts by rule and severity.
- The top five alerts, each with its rule, account, severity and the policy clause from
  `EVIDENCE:policy` (for example "§3.3 structuring").
- End by offering `$aml-investigate <ALERT_ID>` for the top alert.

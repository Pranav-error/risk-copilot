-- Semantic view: business meaning for Cortex Analyst, so "how much cash did high-risk
-- customers deposit in August?" resolves to governed SQL instead of guessed column names.

-- Account-centred view of money movement: one row per (transaction, internal account side).
CREATE OR REPLACE VIEW ACCOUNT_ACTIVITY AS
SELECT TXN_ID, FROM_ACCOUNT_ID AS ACCOUNT_ID, 'OUT' AS DIRECTION, AMOUNT, CHANNEL,
       COUNTERPARTY_COUNTRY, TXN_TS
FROM TRANSACTIONS WHERE FROM_ACCOUNT_ID IS NOT NULL
UNION ALL
SELECT TXN_ID, TO_ACCOUNT_ID, 'IN', AMOUNT, CHANNEL, COUNTERPARTY_COUNTRY, TXN_TS
FROM TRANSACTIONS WHERE TO_ACCOUNT_ID IS NOT NULL;

-- Alerts with their reasons flattened into plain columns.
CREATE OR REPLACE VIEW ALERT_FACTS AS
SELECT ALERT_ID, RULE, ACCOUNT_ID, SEVERITY, EFFECTIVE_SEVERITY, SEVERITY_REASON, STATUS,
       EVIDENCE:policy::STRING AS POLICY_CLAUSE, ARRAY_SIZE(TXN_IDS) AS TXN_COUNT, CREATED_AT
FROM ALERT_QUEUE;

CREATE OR REPLACE SEMANTIC VIEW AML_SEMANTIC_VIEW
  TABLES (
    customers AS CUSTOMERS PRIMARY KEY (CUSTOMER_ID)
      WITH SYNONYMS = ('clients', 'subjects', 'account holders')
      COMMENT = 'Bank customers with KYC profile',
    accounts AS ACCOUNTS PRIMARY KEY (ACCOUNT_ID)
      COMMENT = 'Bank accounts; each belongs to one customer',
    activity AS ACCOUNT_ACTIVITY
      WITH SYNONYMS = ('transactions', 'payments', 'money movement')
      COMMENT = 'Every transaction from the point of view of the internal account it touches',
    alerts AS ALERT_FACTS PRIMARY KEY (ALERT_ID)
      WITH SYNONYMS = ('flags', 'cases', 'red flags')
      COMMENT = 'Alerts raised by deterministic AML rules',
    findings AS FINDINGS PRIMARY KEY (FINDING_ID)
      WITH SYNONYMS = ('decisions', 'dispositions')
      COMMENT = 'Analyst decisions on alerts (append-only)'
  )
  RELATIONSHIPS (
    accounts_to_customers AS accounts (CUSTOMER_ID) REFERENCES customers,
    activity_to_accounts  AS activity (ACCOUNT_ID)  REFERENCES accounts,
    alerts_to_accounts    AS alerts (ACCOUNT_ID)    REFERENCES accounts,
    findings_to_alerts    AS findings (ALERT_ID)    REFERENCES alerts
  )
  FACTS (
    activity.amount AS AMOUNT COMMENT = 'Transaction amount in USD',
    customers.declared_monthly_income AS DECLARED_MONTHLY_INCOME
      COMMENT = 'Expected monthly volume declared at onboarding, USD'
  )
  DIMENSIONS (
    customers.customer_id AS CUSTOMER_ID,
    customers.customer_name AS NAME WITH SYNONYMS = ('customer', 'name') COMMENT = 'Masked for auditors',
    customers.risk_rating AS RISK_RATING WITH SYNONYMS = ('KYC risk', 'customer risk')
      COMMENT = 'LOW, MEDIUM or HIGH',
    customers.customer_type AS CUSTOMER_TYPE COMMENT = 'INDIVIDUAL or BUSINESS',
    customers.pep_flag AS PEP_FLAG WITH SYNONYMS = ('politically exposed person', 'PEP'),
    customers.customer_country AS COUNTRY,
    accounts.account_id AS ACCOUNT_ID,
    accounts.account_type AS ACCOUNT_TYPE,
    activity.txn_id AS TXN_ID,
    activity.direction AS DIRECTION COMMENT = 'IN = money into the account, OUT = money out',
    activity.channel AS CHANNEL COMMENT = 'CASH, WIRE, ACH, UPI or INTERNAL',
    activity.counterparty_country AS COUNTERPARTY_COUNTRY COMMENT = 'ISO-2 country code',
    activity.txn_date AS CAST(TXN_TS AS DATE) WITH SYNONYMS = ('date', 'day'),
    activity.txn_month AS DATE_TRUNC('MONTH', TXN_TS) WITH SYNONYMS = ('month'),
    alerts.alert_id AS ALERT_ID,
    alerts.rule AS RULE WITH SYNONYMS = ('typology', 'alert type', 'detection rule')
      COMMENT = 'STRUCTURING, VELOCITY, PASS_THROUGH, GEO_RISK or ROUND_TRIP_CYCLE',
    alerts.severity AS EFFECTIVE_SEVERITY WITH SYNONYMS = ('severity', 'priority')
      COMMENT = 'CRITICAL, HIGH, MEDIUM or LOW, after the high-risk-customer uplift',
    alerts.status AS STATUS COMMENT = 'OPEN, ESCALATED or DISMISSED',
    alerts.policy_clause AS POLICY_CLAUSE COMMENT = 'Clause of the internal AML policy the rule implements',
    findings.decision AS DECISION COMMENT = 'ESCALATE or DISMISS',
    findings.analyst AS ANALYST
  )
  METRICS (
    activity.total_amount AS SUM(activity.amount) COMMENT = 'Total value moved, USD',
    activity.transaction_count AS COUNT(activity.txn_id),
    activity.cash_deposits AS SUM(IFF(activity.channel = 'CASH' AND activity.direction = 'IN', activity.amount, 0))
      WITH SYNONYMS = ('cash in', 'cash deposited') COMMENT = 'Total cash deposited, USD',
    activity.high_risk_country_wires AS SUM(IFF(activity.channel = 'WIRE'
        AND activity.counterparty_country IN ('KP', 'IR', 'MM'), activity.amount, 0))
      COMMENT = 'Wire value with FATF black-list countries, USD',
    alerts.alert_count AS COUNT(alerts.alert_id) WITH SYNONYMS = ('number of alerts'),
    alerts.open_alerts AS SUM(IFF(alerts.status = 'OPEN', 1, 0)),
    alerts.critical_alerts AS SUM(IFF(alerts.severity = 'CRITICAL', 1, 0)),
    customers.customer_count AS COUNT(customers.customer_id),
    findings.decision_count AS COUNT(findings.decision)
  )
  COMMENT = 'AML monitoring: customers, accounts, money movement, alerts and analyst decisions'
  AI_SQL_GENERATION 'All amounts are USD. "High-risk customer" means RISK_RATING = ''HIGH''. Cash deposit means CHANNEL = ''CASH'' and DIRECTION = ''IN''. Never select from TXN_LABELS: it is evaluation ground truth, not bank data.';

-- Smoke test
-- (no parentheses around the lists: they parse as a multi-column tuple and fail)
SELECT * FROM SEMANTIC_VIEW(
    AML_SEMANTIC_VIEW
    DIMENSIONS alerts.rule
    METRICS alerts.alert_count, alerts.critical_alerts
) ORDER BY 1;

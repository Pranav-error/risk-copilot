-- Deterministic detection rules. Each one is idempotent: it deletes its own rows
-- from ALERTS and re-inserts. Clause numbers refer to corpus/internal_aml_policy.md.
-- Round-trip / layering cycles run separately: CALL DETECT_ROUND_TRIPS();

-- §3.3 STRUCTURING: >=3 cash deposits of $8,000-$9,999.99 within 7 days, total > $10,000.
-- A cash deposit is CHANNEL = 'CASH' into the account (TO_ACCOUNT_ID).
DELETE FROM ALERTS WHERE RULE = 'STRUCTURING';
INSERT INTO ALERTS (RULE, ACCOUNT_ID, TXN_IDS, SEVERITY, EVIDENCE)
WITH dep AS (
    SELECT TXN_ID, TO_ACCOUNT_ID AS ACCOUNT_ID, AMOUNT, TXN_TS
    FROM TRANSACTIONS
    WHERE CHANNEL = 'CASH' AND TO_ACCOUNT_ID IS NOT NULL
      AND AMOUNT >= 8000 AND AMOUNT < 10000
), windows AS (
    -- every deposit anchors a 7-day window; keep the busiest window per account
    SELECT a.ACCOUNT_ID, a.TXN_TS AS WINDOW_START,
           ARRAY_AGG(b.TXN_ID) AS TXN_IDS, COUNT(*) AS N, SUM(b.AMOUNT) AS TOTAL
    FROM dep a
    JOIN dep b ON b.ACCOUNT_ID = a.ACCOUNT_ID
              AND b.TXN_TS >= a.TXN_TS AND b.TXN_TS < a.TXN_TS + INTERVAL '7 days'
    GROUP BY a.ACCOUNT_ID, a.TXN_TS
    HAVING COUNT(*) >= 3 AND SUM(b.AMOUNT) > 10000
    QUALIFY ROW_NUMBER() OVER (PARTITION BY a.ACCOUNT_ID ORDER BY COUNT(*) DESC, SUM(b.AMOUNT) DESC) = 1
)
SELECT 'STRUCTURING', ACCOUNT_ID, TXN_IDS, 'HIGH',
       OBJECT_CONSTRUCT('deposits', N, 'total', TOTAL, 'window_start', WINDOW_START,
                        'threshold', 10000, 'policy', '3.3')
FROM windows;

-- §4.1 VELOCITY: a day's value > 5x the account's trailing 90-day daily average, min $25,000.
-- ponytail: calendar days, not a rolling 24h window; switch to a self-join on TXN_TS if a
-- spike straddling midnight matters.
DELETE FROM ALERTS WHERE RULE = 'VELOCITY';
INSERT INTO ALERTS (RULE, ACCOUNT_ID, TXN_IDS, SEVERITY, EVIDENCE)
WITH legs AS (   -- every transaction counts for both of its internal accounts
    SELECT TXN_ID, FROM_ACCOUNT_ID AS ACCOUNT_ID, AMOUNT, TXN_TS FROM TRANSACTIONS WHERE FROM_ACCOUNT_ID IS NOT NULL
    UNION ALL
    SELECT TXN_ID, TO_ACCOUNT_ID, AMOUNT, TXN_TS FROM TRANSACTIONS WHERE TO_ACCOUNT_ID IS NOT NULL
), daily AS (
    SELECT ACCOUNT_ID, CAST(TXN_TS AS DATE) AS DAY, SUM(AMOUNT) AS VALUE, COUNT(*) AS N
    FROM legs GROUP BY 1, 2
), scored AS (
    SELECT d.ACCOUNT_ID, d.DAY, d.VALUE, d.N,
           COALESCE(SUM(p.VALUE), 0) / 90 AS AVG90
    FROM daily d
    LEFT JOIN daily p ON p.ACCOUNT_ID = d.ACCOUNT_ID
                     AND p.DAY >= d.DAY - INTERVAL '90 days' AND p.DAY < d.DAY
    GROUP BY d.ACCOUNT_ID, d.DAY, d.VALUE, d.N
    HAVING d.VALUE >= 25000 AND d.VALUE > 5 * COALESCE(SUM(p.VALUE), 0) / 90
)
SELECT 'VELOCITY', s.ACCOUNT_ID, ARRAY_AGG(l.TXN_ID),
       CASE WHEN MAX(s.AVG90) = 0 THEN 'HIGH' ELSE 'MEDIUM' END,   -- §4.2: no prior activity
       OBJECT_CONSTRUCT('day', s.DAY, 'day_value', MAX(s.VALUE), 'avg_90d', ROUND(MAX(s.AVG90), 2),
                        'multiple', CASE WHEN MAX(s.AVG90) = 0 THEN NULL ELSE ROUND(MAX(s.VALUE) / MAX(s.AVG90), 1) END,
                        'policy', '4.1')
FROM scored s
JOIN legs l ON l.ACCOUNT_ID = s.ACCOUNT_ID AND CAST(l.TXN_TS AS DATE) = s.DAY
GROUP BY s.ACCOUNT_ID, s.DAY;

-- §6.2 GEO_RISK: any wire with a FATF black-list country (CRITICAL);
-- grey-list wires totalling > $50,000 within 30 days (HIGH).
DELETE FROM ALERTS WHERE RULE = 'GEO_RISK';
INSERT INTO ALERTS (RULE, ACCOUNT_ID, TXN_IDS, SEVERITY, EVIDENCE)
WITH wires AS (
    SELECT t.TXN_ID, COALESCE(t.FROM_ACCOUNT_ID, t.TO_ACCOUNT_ID) AS ACCOUNT_ID,
           t.AMOUNT, t.TXN_TS, t.COUNTERPARTY_COUNTRY, f.LIST
    FROM TRANSACTIONS t
    JOIN FATF_JURISDICTIONS f ON f.COUNTRY_CODE = t.COUNTERPARTY_COUNTRY
    WHERE t.CHANNEL = 'WIRE'
), black AS (
    SELECT ACCOUNT_ID, ARRAY_AGG(TXN_ID) AS TXN_IDS, SUM(AMOUNT) AS TOTAL,
           ARRAY_AGG(DISTINCT COUNTERPARTY_COUNTRY) AS COUNTRIES
    FROM wires WHERE LIST = 'BLACK' GROUP BY ACCOUNT_ID
), grey AS (
    SELECT a.ACCOUNT_ID, a.TXN_TS AS WINDOW_START, ARRAY_AGG(b.TXN_ID) AS TXN_IDS,
           SUM(b.AMOUNT) AS TOTAL, ARRAY_AGG(DISTINCT b.COUNTERPARTY_COUNTRY) AS COUNTRIES
    FROM wires a
    JOIN wires b ON b.ACCOUNT_ID = a.ACCOUNT_ID AND b.LIST = 'GREY'
                AND b.TXN_TS >= a.TXN_TS AND b.TXN_TS < a.TXN_TS + INTERVAL '30 days'
    WHERE a.LIST = 'GREY'
    GROUP BY a.ACCOUNT_ID, a.TXN_TS
    HAVING SUM(b.AMOUNT) > 50000
    QUALIFY ROW_NUMBER() OVER (PARTITION BY a.ACCOUNT_ID ORDER BY SUM(b.AMOUNT) DESC) = 1
)
SELECT 'GEO_RISK', ACCOUNT_ID, TXN_IDS, 'CRITICAL',
       OBJECT_CONSTRUCT('list', 'FATF black list', 'countries', COUNTRIES, 'total', TOTAL, 'policy', '6.2')
FROM black
UNION ALL
SELECT 'GEO_RISK', ACCOUNT_ID, TXN_IDS, 'HIGH',
       OBJECT_CONSTRUCT('list', 'FATF grey list', 'countries', COUNTRIES, 'total_30d', TOTAL,
                        'window_start', WINDOW_START, 'threshold', 50000, 'policy', '6.2')
FROM grey;

-- §7.2: HIGH-risk customers' alerts are raised one severity level. A view, not an UPDATE,
-- so re-running the rules never escalates the same alert twice. The app reads this.
CREATE OR REPLACE VIEW ALERT_QUEUE AS
SELECT al.*, c.CUSTOMER_ID, c.RISK_RATING,
       CASE WHEN c.RISK_RATING = 'HIGH' THEN
            CASE al.SEVERITY WHEN 'LOW' THEN 'MEDIUM' WHEN 'MEDIUM' THEN 'HIGH' ELSE 'CRITICAL' END
            ELSE al.SEVERITY END AS EFFECTIVE_SEVERITY
FROM ALERTS al
LEFT JOIN ACCOUNTS a  ON a.ACCOUNT_ID = al.ACCOUNT_ID
LEFT JOIN CUSTOMERS c ON c.CUSTOMER_ID = a.CUSTOMER_ID;

-- One row per (alert, transaction): used by the investigate skill and the evaluation.
CREATE OR REPLACE VIEW ALERT_TXNS AS
SELECT a.ALERT_ID, a.RULE, f.VALUE::STRING AS TXN_ID
FROM ALERTS a, LATERAL FLATTEN(input => a.TXN_IDS) f;

-- Deterministic detection rules. Clause numbers refer to corpus/internal_aml_policy.md.
-- Round-trip / layering cycles run separately: CALL DETECT_ROUND_TRIPS();
--
-- Rules write candidates; the last step adds only alerts not already in ALERTS (keyed on rule +
-- the exact set of transactions). Rerunning never renumbers an alert or resets its STATUS, so
-- FINDINGS and SAR_REPORTS keep pointing at the alert the analyst actually reviewed.
-- Every "keep the best window" has a total order (score, then earliest, then txn id): with
-- ties, Snowflake picks a different row each run and the same pattern gets a second alert.
-- Scores are sums rounded to cents: on an engine that sums floats, equal windows otherwise
-- differ in the last bit depending on row order (found on the held-out data in DuckDB).
-- ponytail: a stale OPEN alert whose pattern no longer fires is kept, not removed; add a
-- RESOLVED_BY_RERUN status if the data starts changing under existing alerts.

CREATE OR REPLACE TRANSIENT TABLE ALERT_CANDIDATES (
    RULE STRING, ACCOUNT_ID STRING, TXN_IDS ARRAY, SEVERITY STRING, EVIDENCE VARIANT) COPY GRANTS;

-- §3.3 STRUCTURING: >=3 cash deposits of $8,000-$9,999.99 within 7 days, total > $10,000.
-- A cash deposit is CHANNEL = 'CASH' into the account (TO_ACCOUNT_ID).
INSERT INTO ALERT_CANDIDATES (RULE, ACCOUNT_ID, TXN_IDS, SEVERITY, EVIDENCE)
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
    GROUP BY a.ACCOUNT_ID, a.TXN_TS, a.TXN_ID
    HAVING COUNT(*) >= 3 AND SUM(b.AMOUNT) > 10000
    QUALIFY ROW_NUMBER() OVER (PARTITION BY a.ACCOUNT_ID ORDER BY COUNT(*) DESC, ROUND(SUM(b.AMOUNT), 2) DESC, a.TXN_TS, a.TXN_ID) = 1
)
SELECT 'STRUCTURING', ACCOUNT_ID, TXN_IDS, 'HIGH',
       OBJECT_CONSTRUCT('deposits', N, 'total', TOTAL, 'window_start', WINDOW_START,
                        'threshold', 10000, 'policy', '3.3')
FROM windows;

-- §4.1 VELOCITY: 4+ transactions within any 24 hours whose value is > 5x the account's
-- trailing 90-day daily average, minimum $10,000. Rolling window, so a burst that crosses
-- midnight isn't split in two.
INSERT INTO ALERT_CANDIDATES (RULE, ACCOUNT_ID, TXN_IDS, SEVERITY, EVIDENCE)
WITH legs AS (   -- every transaction counts for both of its internal accounts
    SELECT TXN_ID, FROM_ACCOUNT_ID AS ACCOUNT_ID, AMOUNT, TXN_TS FROM TRANSACTIONS WHERE FROM_ACCOUNT_ID IS NOT NULL
    UNION ALL
    SELECT TXN_ID, TO_ACCOUNT_ID, AMOUNT, TXN_TS FROM TRANSACTIONS WHERE TO_ACCOUNT_ID IS NOT NULL
), bursts AS (
    SELECT a.ACCOUNT_ID, a.TXN_ID AS ANCHOR, a.TXN_TS AS WINDOW_START,
           COUNT(*) AS N, ROUND(SUM(b.AMOUNT), 2) AS VALUE   -- cents: float sums must not break ties
    FROM legs a
    JOIN legs b ON b.ACCOUNT_ID = a.ACCOUNT_ID
               AND b.TXN_TS >= a.TXN_TS AND b.TXN_TS < a.TXN_TS + INTERVAL '24 hours'
    GROUP BY a.ACCOUNT_ID, a.TXN_ID, a.TXN_TS
    HAVING COUNT(*) >= 4 AND SUM(b.AMOUNT) >= 10000
), scored AS (
    SELECT w.ACCOUNT_ID, w.ANCHOR, w.WINDOW_START, w.N, w.VALUE,
           COALESCE(SUM(p.AMOUNT), 0) / 90 AS AVG90
    FROM bursts w
    LEFT JOIN legs p ON p.ACCOUNT_ID = w.ACCOUNT_ID
                    AND p.TXN_TS >= w.WINDOW_START - INTERVAL '90 days' AND p.TXN_TS < w.WINDOW_START
    GROUP BY w.ACCOUNT_ID, w.ANCHOR, w.WINDOW_START, w.N, w.VALUE
    HAVING w.VALUE > 5 * COALESCE(SUM(p.AMOUNT), 0) / 90
    QUALIFY ROW_NUMBER() OVER (PARTITION BY w.ACCOUNT_ID ORDER BY w.VALUE DESC, w.WINDOW_START, w.ANCHOR) = 1
)
SELECT 'VELOCITY', s.ACCOUNT_ID, ARRAY_AGG(l.TXN_ID),
       CASE WHEN MAX(s.AVG90) = 0 THEN 'HIGH' ELSE 'MEDIUM' END,   -- §4.2: no prior activity
       OBJECT_CONSTRUCT('window_start', MAX(s.WINDOW_START), 'txns_in_24h', MAX(s.N),
                        'value_24h', MAX(s.VALUE), 'avg_daily_90d', ROUND(MAX(s.AVG90), 2),
                        'multiple', CASE WHEN MAX(s.AVG90) = 0 THEN NULL ELSE ROUND(MAX(s.VALUE) / MAX(s.AVG90), 1) END,
                        'policy', '4.1')
FROM scored s
JOIN legs l ON l.ACCOUNT_ID = s.ACCOUNT_ID
           AND l.TXN_TS >= s.WINDOW_START AND l.TXN_TS < s.WINDOW_START + INTERVAL '24 hours'
GROUP BY s.ACCOUNT_ID;

-- §5.5 PASS_THROUGH (rapid layering): an inflow of $50,000+ followed within 72 hours by 2+
-- outflows totalling at least 50% of it. Money that only passes through the account.
INSERT INTO ALERT_CANDIDATES (RULE, ACCOUNT_ID, TXN_IDS, SEVERITY, EVIDENCE)
WITH inflow AS (
    SELECT TXN_ID, TO_ACCOUNT_ID AS ACCOUNT_ID, AMOUNT, TXN_TS
    FROM TRANSACTIONS WHERE TO_ACCOUNT_ID IS NOT NULL AND AMOUNT >= 50000
), passed AS (
    SELECT i.ACCOUNT_ID, i.TXN_ID AS IN_TXN, i.AMOUNT AS IN_AMOUNT, i.TXN_TS AS IN_TS,
           ARRAY_AGG(o.TXN_ID) AS OUT_TXNS, COUNT(*) AS N_OUT, SUM(o.AMOUNT) AS OUT_TOTAL,
           MAX(o.TXN_TS) AS LAST_OUT_TS
    FROM inflow i
    JOIN TRANSACTIONS o ON o.FROM_ACCOUNT_ID = i.ACCOUNT_ID
                       AND o.TXN_TS > i.TXN_TS AND o.TXN_TS <= i.TXN_TS + INTERVAL '72 hours'
    GROUP BY i.ACCOUNT_ID, i.TXN_ID, i.AMOUNT, i.TXN_TS
    HAVING COUNT(*) >= 2 AND SUM(o.AMOUNT) >= 0.5 * i.AMOUNT
    QUALIFY ROW_NUMBER() OVER (PARTITION BY i.ACCOUNT_ID ORDER BY i.AMOUNT DESC, i.TXN_TS, i.TXN_ID) = 1
)
SELECT 'PASS_THROUGH', ACCOUNT_ID, ARRAY_PREPEND(OUT_TXNS, IN_TXN), 'HIGH',
       OBJECT_CONSTRUCT('inflow', IN_AMOUNT, 'outflow_total', OUT_TOTAL, 'outflows', N_OUT,
                        'pct_passed', ROUND(100 * OUT_TOTAL / IN_AMOUNT, 1),
                        'hours', ROUND(DATEDIFF('minute', IN_TS, LAST_OUT_TS) / 60, 1), 'policy', '5.5')
FROM passed;

-- §6.2 GEO_RISK: any wire with a FATF black-list country (CRITICAL);
-- grey-list wires totalling > $50,000 within 30 days (HIGH).
INSERT INTO ALERT_CANDIDATES (RULE, ACCOUNT_ID, TXN_IDS, SEVERITY, EVIDENCE)
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
    GROUP BY a.ACCOUNT_ID, a.TXN_TS, a.TXN_ID
    HAVING SUM(b.AMOUNT) > 50000
    QUALIFY ROW_NUMBER() OVER (PARTITION BY a.ACCOUNT_ID ORDER BY ROUND(SUM(b.AMOUNT), 2) DESC, a.TXN_TS, a.TXN_ID) = 1
)
SELECT 'GEO_RISK', ACCOUNT_ID, TXN_IDS, 'CRITICAL',
       OBJECT_CONSTRUCT('list', 'FATF black list', 'countries', COUNTRIES, 'total', TOTAL, 'policy', '6.2')
FROM black
UNION ALL
SELECT 'GEO_RISK', ACCOUNT_ID, TXN_IDS, 'HIGH',
       OBJECT_CONSTRUCT('list', 'FATF grey list', 'countries', COUNTRIES, 'total_30d', TOTAL,
                        'window_start', WINDOW_START, 'threshold', 50000, 'policy', '6.2')
FROM grey;

INSERT INTO ALERTS (RULE, ACCOUNT_ID, TXN_IDS, SEVERITY, EVIDENCE, ALERT_KEY)
SELECT RULE, ACCOUNT_ID, TXN_IDS, SEVERITY, EVIDENCE,
       MD5(RULE || '|' || ARRAY_TO_STRING(ARRAY_SORT(TXN_IDS), ','))
FROM ALERT_CANDIDATES
WHERE MD5(RULE || '|' || ARRAY_TO_STRING(ARRAY_SORT(TXN_IDS), ','))
      NOT IN (SELECT ALERT_KEY FROM ALERTS WHERE ALERT_KEY IS NOT NULL);

-- §7.2: HIGH-risk customers' alerts are raised one severity level. A view, not an UPDATE,
-- so re-running the rules never escalates the same alert twice. The app reads this.
CREATE OR REPLACE VIEW ALERT_QUEUE COPY GRANTS AS
SELECT al.*, c.CUSTOMER_ID, c.RISK_RATING,
       CASE WHEN c.RISK_RATING = 'HIGH' THEN
            CASE al.SEVERITY WHEN 'LOW' THEN 'MEDIUM' WHEN 'MEDIUM' THEN 'HIGH' ELSE 'CRITICAL' END
            ELSE al.SEVERITY END AS EFFECTIVE_SEVERITY,
       -- stated as data so an explanation reads the reason instead of inventing one
       CASE WHEN c.RISK_RATING = 'HIGH' AND al.SEVERITY <> 'CRITICAL'
            THEN 'Raised one level from ' || al.SEVERITY || ': customer risk rating is HIGH (policy 7.2)'
            ELSE 'As raised by rule ' || al.RULE || ' (policy ' || al.EVIDENCE:policy::STRING || ')'
            END AS SEVERITY_REASON
FROM ALERTS al
LEFT JOIN ACCOUNTS a  ON a.ACCOUNT_ID = al.ACCOUNT_ID
LEFT JOIN CUSTOMERS c ON c.CUSTOMER_ID = a.CUSTOMER_ID;

-- One row per (alert, transaction): used by the investigate skill and the evaluation.
CREATE OR REPLACE VIEW ALERT_TXNS COPY GRANTS AS
SELECT a.ALERT_ID, a.RULE, f.VALUE::STRING AS TXN_ID
FROM ALERTS a, LATERAL FLATTEN(input => a.TXN_IDS) f;

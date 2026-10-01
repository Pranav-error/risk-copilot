-- Precision / recall of each rule against the seeded ground truth (TXN_LABELS).
-- Only this file reads TXN_LABELS; detection rules never do.
--   precision          = alerts containing at least one labelled (truly suspicious) txn / alerts
--   typology_precision = same, but the label must match the rule's own typology. Lower for
--                        VELOCITY by design: a spike on a cycle or FATF account is a real catch
--                        under another name, not a false positive
--   recall    = labelled txns that appear in a matching alert / labelled txns

CREATE OR REPLACE VIEW RULE_TYPOLOGY AS
SELECT * FROM VALUES
    ('STRUCTURING', 'STRUCTURING'), ('VELOCITY', 'VELOCITY'), ('GEO_RISK', 'GEO_RISK'),
    ('ROUND_TRIP_CYCLE', 'ROUND_TRIP'), ('ROUND_TRIP_CYCLE', 'LAYERING')
    AS v(RULE, TYPOLOGY);

CREATE OR REPLACE VIEW RULE_METRICS AS
WITH hits AS (
    SELECT DISTINCT x.ALERT_ID, x.RULE, x.TXN_ID, l.TYPOLOGY
    FROM ALERT_TXNS x
    JOIN RULE_TYPOLOGY rt ON rt.RULE = x.RULE
    JOIN TXN_LABELS l ON l.TXN_ID = x.TXN_ID AND l.TYPOLOGY = rt.TYPOLOGY
), any_hit AS (
    SELECT DISTINCT x.ALERT_ID FROM ALERT_TXNS x JOIN TXN_LABELS l ON l.TXN_ID = x.TXN_ID
), prec AS (
    SELECT a.RULE, COUNT(DISTINCT a.ALERT_ID) AS ALERTS,
           COUNT(DISTINCT y.ALERT_ID) AS TRUE_ALERTS, COUNT(DISTINCT h.ALERT_ID) AS TYPOLOGY_ALERTS
    FROM ALERTS a
    LEFT JOIN any_hit y ON y.ALERT_ID = a.ALERT_ID
    LEFT JOIN hits h ON h.ALERT_ID = a.ALERT_ID
    GROUP BY a.RULE
), rec AS (
    SELECT rt.RULE, COUNT(DISTINCT l.TXN_ID) AS LABELLED_TXNS, COUNT(DISTINCT h.TXN_ID) AS CAUGHT_TXNS
    FROM RULE_TYPOLOGY rt
    JOIN TXN_LABELS l ON l.TYPOLOGY = rt.TYPOLOGY
    LEFT JOIN hits h ON h.TXN_ID = l.TXN_ID AND h.RULE = rt.RULE
    GROUP BY rt.RULE
)
SELECT r.RULE, COALESCE(p.ALERTS, 0) AS ALERTS, COALESCE(p.TRUE_ALERTS, 0) AS TRUE_ALERTS,
       ROUND(p.TRUE_ALERTS / NULLIF(p.ALERTS, 0), 3) AS PRECISION,
       ROUND(p.TYPOLOGY_ALERTS / NULLIF(p.ALERTS, 0), 3) AS TYPOLOGY_PRECISION,
       r.LABELLED_TXNS, r.CAUGHT_TXNS,
       ROUND(r.CAUGHT_TXNS / NULLIF(r.LABELLED_TXNS, 0), 3) AS RECALL
FROM rec r LEFT JOIN prec p ON p.RULE = r.RULE
ORDER BY r.RULE;

SELECT * FROM RULE_METRICS;

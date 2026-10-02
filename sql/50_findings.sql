-- The documented-finding end of the flow: analyst decisions and SAR drafts.
-- Append-only in practice: the analyst role gets INSERT + SELECT only, never UPDATE/DELETE
-- (granted in the governance script), so a decision can't be rewritten after the fact.

CREATE TABLE IF NOT EXISTS FINDINGS (
    FINDING_ID       NUMBER AUTOINCREMENT PRIMARY KEY,
    ALERT_ID         NUMBER  NOT NULL,
    DECISION         STRING  NOT NULL,   -- ESCALATE / DISMISS
    ANALYST          STRING  NOT NULL,
    REASON           STRING  NOT NULL,
    EVIDENCE_TXN_IDS ARRAY,
    POLICY_REFS      ARRAY,
    DECIDED_BY_ROLE  STRING  DEFAULT CURRENT_ROLE(),
    DECIDED_AT       TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

CREATE TABLE IF NOT EXISTS SAR_REPORTS (
    SAR_ID         NUMBER AUTOINCREMENT PRIMARY KEY,
    ALERT_ID       NUMBER  NOT NULL,
    STATUS         STRING  DEFAULT 'DRAFT',   -- DRAFT / APPROVED / FILED
    NARRATIVE      STRING  NOT NULL,
    CITED_TXN_IDS  ARRAY,
    CITED_SOURCES  ARRAY,
    DRAFTED_BY     STRING,
    CREATED_AT     TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- Machine check on every SAR: each transaction ID in the narrative must exist and belong to
-- the case (the alert's own evidence, or a transaction touching the alerted account).
-- The sar-draft skill runs this after saving; anything in UNVERIFIED blocks approval.
CREATE OR REPLACE VIEW SAR_CITATION_CHECK AS
WITH cited AS (
    SELECT s.SAR_ID, s.ALERT_ID, f.VALUE::STRING AS TXN_ID
    FROM SAR_REPORTS s,
         LATERAL FLATTEN(input => REGEXP_SUBSTR_ALL(s.NARRATIVE, 'TXN_[0-9A-F]{12}')) f
), checked AS (
    SELECT DISTINCT c.SAR_ID, c.ALERT_ID, c.TXN_ID,
           t.TXN_ID IS NOT NULL AS TXN_EXISTS,
           (ARRAY_CONTAINS(c.TXN_ID::VARIANT, a.TXN_IDS)
            OR t.FROM_ACCOUNT_ID = a.ACCOUNT_ID OR t.TO_ACCOUNT_ID = a.ACCOUNT_ID) AS IN_CASE
    FROM cited c
    JOIN ALERTS a ON a.ALERT_ID = c.ALERT_ID
    LEFT JOIN TRANSACTIONS t ON t.TXN_ID = c.TXN_ID
)
SELECT SAR_ID, ALERT_ID,
       COUNT(*) AS TXNS_CITED,
       COUNT_IF(TXN_EXISTS AND IN_CASE) AS VERIFIED,
       ARRAY_AGG(CASE WHEN NOT (TXN_EXISTS AND COALESCE(IN_CASE, FALSE)) THEN TXN_ID END) AS UNVERIFIED
FROM checked
GROUP BY SAR_ID, ALERT_ID;

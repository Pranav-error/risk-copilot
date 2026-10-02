-- Shared ALERTS table: every detection rule (SQL or Snowpark) writes here.
-- Run after the data layer (TRANSACTIONS etc.) is loaded, in the same schema.

CREATE TABLE IF NOT EXISTS ALERTS (
    ALERT_ID    NUMBER AUTOINCREMENT PRIMARY KEY,
    RULE        STRING  NOT NULL,       -- STRUCTURING, VELOCITY, GEO_RISK, ROUND_TRIP_CYCLE, ...
    ACCOUNT_ID  STRING  NOT NULL,
    TXN_IDS     ARRAY   NOT NULL,       -- evidence: exact transactions that fired the rule
    SEVERITY    STRING  NOT NULL,       -- CRITICAL / HIGH / MEDIUM / LOW
    EVIDENCE    VARIANT,                -- rule-specific detail (path, totals, thresholds)
    STATUS      STRING  DEFAULT 'OPEN', -- OPEN / ESCALATED / DISMISSED
    ALERT_KEY   STRING,                 -- MD5(rule | sorted txn ids): same pattern = same alert
    CREATED_AT  TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);
ALTER TABLE ALERTS ADD COLUMN IF NOT EXISTS ALERT_KEY STRING;

-- Upload the code first, from the repo root:
--   PUT file://detection/cycles.py @CODE_STAGE AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
CREATE STAGE IF NOT EXISTS CODE_STAGE;

CREATE OR REPLACE PROCEDURE DETECT_ROUND_TRIPS()
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python')
IMPORTS = ('@CODE_STAGE/cycles.py')
HANDLER = 'cycles.run';

CALL DETECT_ROUND_TRIPS();
SELECT RULE, ACCOUNT_ID, SEVERITY, EVIDENCE:path, EVIDENCE:hours_elapsed
FROM ALERTS WHERE RULE = 'ROUND_TRIP_CYCLE' ORDER BY ALERT_ID;

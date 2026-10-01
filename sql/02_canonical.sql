-- Map the raw generated data onto the data contract (README) that every rule,
-- the semantic view and the app depend on. Raw stays untouched; rerun freely.

CREATE OR REPLACE TABLE CUSTOMERS AS
SELECT customer_id                AS CUSTOMER_ID,
       full_name                  AS NAME,
       UPPER(customer_type)       AS CUSTOMER_TYPE,
       UPPER(kyc_risk_rating)     AS RISK_RATING,
       pep_flag                   AS PEP_FLAG,
       business_sector            AS OCCUPATION,
       expected_monthly_volume    AS DECLARED_MONTHLY_INCOME,   -- expected volume, used as the §7.3 baseline
       country                    AS COUNTRY,
       onboarding_date            AS ONBOARDED_AT
FROM RAW_CUSTOMERS;

CREATE OR REPLACE TABLE ACCOUNTS AS
SELECT account_id AS ACCOUNT_ID, customer_id AS CUSTOMER_ID, UPPER(account_type) AS ACCOUNT_TYPE,
       open_date AS OPENED_AT, UPPER(status) AS STATUS
FROM RAW_ACCOUNTS;

-- Raw rows are one-sided (account + counterparty). Direction comes from txn_type, or the
-- description for transfers ("incoming wire"); anything unstated is treated as outgoing.
-- Amounts: the generator writes INR, but the typologies are sized for the US BSA
-- thresholds the policy uses (structuring at 9,000-9,900 under a 10,000 CTR line), so the
-- canonical table reads them as USD. Change here if the generator switches to RBI thresholds.
CREATE OR REPLACE TABLE TRANSACTIONS AS
WITH r AS (
    SELECT t.*,
           t.txn_type = 'deposit' OR LOWER(t.description) LIKE 'incoming%' AS IS_INBOUND,
           (a.account_id IS NOT NULL) AS CPTY_INTERNAL
    FROM RAW_TRANSACTIONS t
    LEFT JOIN RAW_ACCOUNTS a ON a.account_id = t.counterparty_account
)
SELECT txn_id AS TXN_ID,
       CASE WHEN IS_INBOUND THEN (CASE WHEN CPTY_INTERNAL THEN counterparty_account END)
            ELSE account_id END                                        AS FROM_ACCOUNT_ID,
       CASE WHEN IS_INBOUND THEN account_id
            ELSE (CASE WHEN CPTY_INTERNAL THEN counterparty_account END) END AS TO_ACCOUNT_ID,
       counterparty_account                                            AS COUNTERPARTY_REF,
       amount                                                          AS AMOUNT,
       'USD'                                                           AS CURRENCY,
       CASE WHEN LOWER(description) LIKE 'cash%' THEN 'CASH'
            WHEN txn_type IN ('deposit', 'withdrawal') AND channel IN ('atm', 'branch') THEN 'CASH'
            WHEN txn_type = 'wire' THEN 'WIRE'
            WHEN CPTY_INTERNAL THEN 'INTERNAL'
            WHEN txn_type = 'upi' THEN 'UPI'
            ELSE 'ACH' END                                             AS CHANNEL,
       UPPER(channel)                                                  AS ACCESS_POINT,
       counterparty_country                                            AS COUNTERPARTY_COUNTRY,
       txn_timestamp                                                   AS TXN_TS,
       description                                                     AS DESCRIPTION
FROM r;

CREATE OR REPLACE TABLE TXN_LABELS AS
SELECT txn_id AS TXN_ID,
       account_id AS ACCOUNT_ID,
       CASE typology_type
            WHEN 'VELOCITY_ANOMALY' THEN 'VELOCITY'
            WHEN 'GEOGRAPHIC_RISK'  THEN 'GEO_RISK'
            WHEN 'RAPID_LAYERING'   THEN 'LAYERING'
            WHEN 'ROUND_TRIPPING'   THEN 'ROUND_TRIP'
            ELSE typology_type END AS TYPOLOGY
FROM RAW_LABELS;

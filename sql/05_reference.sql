-- FATF high-risk jurisdictions, used by the GEO_RISK rule (policy §6.2).
-- Source: FATF plenary outcomes, 17-19 June 2026 (Paris): black list unchanged; grey list
-- 22 jurisdictions after adding Bosnia and Herzegovina and Iraq and removing Algeria and
-- Namibia. Cross-checked against two independent published lists on 2026-10-02.
-- FATF updates these three times a year (Feb / Jun / Oct): refresh after each plenary.
CREATE OR REPLACE TABLE FATF_JURISDICTIONS (
    COUNTRY_CODE STRING,   -- ISO 3166-1 alpha-2, same as TRANSACTIONS.COUNTERPARTY_COUNTRY
    COUNTRY_NAME STRING,
    LIST         STRING,   -- BLACK = call for action, GREY = increased monitoring
    SOURCE       STRING
) COPY GRANTS;

INSERT INTO FATF_JURISDICTIONS VALUES
('KP','North Korea','BLACK','FATF Jun 2026'), ('IR','Iran','BLACK','FATF Jun 2026'),
('MM','Myanmar','BLACK','FATF Jun 2026'),
('AO','Angola','GREY','FATF Jun 2026'), ('BO','Bolivia','GREY','FATF Jun 2026'),
('BA','Bosnia and Herzegovina','GREY','FATF Jun 2026'), ('BG','Bulgaria','GREY','FATF Jun 2026'),
('CM','Cameroon','GREY','FATF Jun 2026'), ('CI','Cote d''Ivoire','GREY','FATF Jun 2026'),
('CD','DR Congo','GREY','FATF Jun 2026'), ('HT','Haiti','GREY','FATF Jun 2026'),
('IQ','Iraq','GREY','FATF Jun 2026'), ('KE','Kenya','GREY','FATF Jun 2026'),
('KW','Kuwait','GREY','FATF Jun 2026'), ('LA','Laos','GREY','FATF Jun 2026'),
('LB','Lebanon','GREY','FATF Jun 2026'), ('MC','Monaco','GREY','FATF Jun 2026'),
('NP','Nepal','GREY','FATF Jun 2026'), ('PG','Papua New Guinea','GREY','FATF Jun 2026'),
('SS','South Sudan','GREY','FATF Jun 2026'), ('SY','Syria','GREY','FATF Jun 2026'),
('VE','Venezuela','GREY','FATF Jun 2026'), ('VN','Vietnam','GREY','FATF Jun 2026'),
('VG','British Virgin Islands','GREY','FATF Jun 2026'), ('YE','Yemen','GREY','FATF Jun 2026');

-- The bank's own high-risk list (raw reference) on top of FATF: countries it names that
-- FATF doesn't list are added as GREY. Needs 01_load_raw.sql.
INSERT INTO FATF_JURISDICTIONS (COUNTRY_CODE, COUNTRY_NAME, LIST, SOURCE)
SELECT country_code, country_code, 'GREY', 'bank internal list' FROM RAW_HIGH_RISK_COUNTRIES
WHERE country_code NOT IN (SELECT COUNTRY_CODE FROM FATF_JURISDICTIONS);

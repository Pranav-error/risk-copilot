-- FATF high-risk jurisdictions, used by the GEO_RISK rule (policy §6.2).
-- Source: FATF public statements, June 2025 plenary. FATF updates these three times a
-- year: replace from the current statement in corpus/ before the demo.
CREATE OR REPLACE TABLE FATF_JURISDICTIONS (
    COUNTRY_CODE STRING,   -- ISO 3166-1 alpha-2, same as TRANSACTIONS.COUNTERPARTY_COUNTRY
    COUNTRY_NAME STRING,
    LIST         STRING    -- BLACK = call for action, GREY = increased monitoring
);

INSERT INTO FATF_JURISDICTIONS VALUES
('KP','North Korea','BLACK'), ('IR','Iran','BLACK'), ('MM','Myanmar','BLACK'),
('DZ','Algeria','GREY'), ('AO','Angola','GREY'), ('BO','Bolivia','GREY'),
('BG','Bulgaria','GREY'), ('BF','Burkina Faso','GREY'), ('CM','Cameroon','GREY'),
('CI','Cote d''Ivoire','GREY'), ('HR','Croatia','GREY'), ('CD','DR Congo','GREY'),
('HT','Haiti','GREY'), ('KE','Kenya','GREY'), ('LA','Laos','GREY'),
('LB','Lebanon','GREY'), ('MC','Monaco','GREY'), ('MZ','Mozambique','GREY'),
('NA','Namibia','GREY'), ('NP','Nepal','GREY'), ('NG','Nigeria','GREY'),
('ZA','South Africa','GREY'), ('SS','South Sudan','GREY'), ('SY','Syria','GREY'),
('TZ','Tanzania','GREY'), ('VE','Venezuela','GREY'), ('VN','Vietnam','GREY'),
('VG','British Virgin Islands','GREY'), ('YE','Yemen','GREY');

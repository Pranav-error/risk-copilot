-- Cortex Search over the regulatory corpus, so every explanation can cite a clause.
-- Pattern from Snowflake's Cortex Search tutorial (AI_PARSE_DOCUMENT -> chunk -> service).
--
-- Upload first, from the repo root:
--   PUT file://corpus/*.pdf @REG_DOCS AUTO_COMPRESS=FALSE OVERWRITE=TRUE;
--   ALTER STAGE REG_DOCS REFRESH;

CREATE STAGE IF NOT EXISTS REG_DOCS
    DIRECTORY = (ENABLE = TRUE)
    ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE');

CREATE OR REPLACE TABLE REG_DOCS_RAW AS
SELECT RELATIVE_PATH,
       TO_VARCHAR(AI_PARSE_DOCUMENT(TO_FILE('@REG_DOCS', RELATIVE_PATH), {'mode': 'LAYOUT'}):content) AS CONTENT
FROM DIRECTORY('@REG_DOCS')
WHERE RELATIVE_PATH LIKE '%.pdf';

-- Chunks keep their source file and section headers so an answer can say
-- "internal_aml_policy.pdf, §3 Structuring" rather than just quoting text.
CREATE OR REPLACE TABLE REG_DOC_CHUNKS AS
SELECT RELATIVE_PATH AS SOURCE,
       COALESCE(c.value['headers']['header_1']::STRING, '') AS SECTION,
       COALESCE(c.value['headers']['header_2']::STRING, '') AS SUBSECTION,
       RELATIVE_PATH || '\n' || COALESCE(c.value['headers']['header_1']::STRING || '\n', '')
           || COALESCE(c.value['headers']['header_2']::STRING || '\n', '') || c.value['chunk']::STRING AS CHUNK
FROM REG_DOCS_RAW,
     LATERAL FLATTEN(SNOWFLAKE.CORTEX.SPLIT_TEXT_MARKDOWN_HEADER(
         CONTENT, OBJECT_CONSTRUCT('#', 'header_1', '##', 'header_2'), 2000, 300)) c;

CREATE OR REPLACE CORTEX SEARCH SERVICE AML_POLICY_SEARCH
    ON CHUNK
    ATTRIBUTES SOURCE, SECTION
    WAREHOUSE = COMPUTE_WH
    TARGET_LAG = '1 day'
    AS (SELECT CHUNK, SOURCE, SECTION, SUBSECTION FROM REG_DOC_CHUNKS);

-- Smoke test: should return the structuring clause (policy §3.3) first.
SELECT PARSE_JSON(SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
    'AML_POLICY_SEARCH',
    '{"query": "cash deposits just below the reporting threshold", "columns": ["SOURCE", "SECTION", "CHUNK"], "limit": 3}'
)):results AS RESULTS;

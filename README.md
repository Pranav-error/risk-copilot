# Risk Copilot — Fraud, AML & Regulatory Intelligence on Snowflake

Snowflake CoCo CLI Hackathon 2026 (GCC Edition) — Problem Statement #1.

Signal → evidence → documented finding → SAR draft, for AML analysts at GCC risk-ops
teams serving global banks. **Detection is deterministic (SQL + Snowpark); the LLM only
explains and drafts, never decides what is fraud.**

## Layout

| Path | What | Owner |
|---|---|---|
| `data/` | Synthetic data generator + load into Snowflake | Udith |
| `sql/05_reference.sql` | FATF black/grey list (`FATF_JURISDICTIONS`) | Pranav |
| `sql/10_alerts_and_cycle_proc.sql` | Shared `ALERTS` table + registers the cycle proc | Pranav |
| `sql/20_detection_rules.sql` | Structuring, velocity, geo-risk rules; `ALERT_QUEUE`, `ALERT_TXNS` views | Pranav |
| `sql/30_evaluate.sql` | Precision/recall per rule vs `TXN_LABELS` → `RULE_METRICS` | Pranav |
| `sql/40_cortex_search.sql` | Parse + chunk `corpus/` PDFs → `AML_POLICY_SEARCH` service | Pranav |
| `sql/50_findings.sql` | `FINDINGS` (analyst decisions) + `SAR_REPORTS` | Pranav |
| `detection/cycles.py` | Layering / round-trip cycle detection (Snowpark proc) | Pranav |
| `.cortex/skills/` | CoCo skills: `$aml-detect`, `$aml-investigate`, `$sar-draft` | Pranav |
| `corpus/` | Documents indexed by Cortex Search (see Sources) | Pranav |
| `docs/brief.txt` | Submission brief (≤1024 chars) | both |
| *todo* | Semantic view + Cortex Agent, masking + roles, Streamlit app | |

## Run order (once data is loaded)

```
05_reference → 10_alerts_and_cycle_proc → 20_detection_rules → 40_cortex_search → 50_findings
then in CoCo:  $aml-detect   →   $aml-investigate <id>   →   $sar-draft <id>
```

## Tests (no Snowflake needed)

```
python3 detection/cycles.py                         # cycle detector self-check
pip install duckdb && python3 tests/test_rules_duckdb.py   # all rules + evaluation on planted data
```

On planted data (35K txns, 268 labelled): every rule recall 1.0; precision 1.0, velocity 0.993.
`TYPOLOGY_PRECISION` for velocity is 0.14 because a spike on a cycle or FATF account is a real
catch filed under another rule. Re-measure on the real synthetic set.

## Data contract (all detection, the semantic model and the app depend on these names)

All amounts in **USD** (policy thresholds are BSA/FinCEN: $10K CTR etc.).

```
CUSTOMERS    (CUSTOMER_ID, NAME, RISK_RATING, OCCUPATION, DECLARED_MONTHLY_INCOME, COUNTRY, ONBOARDED_AT)
ACCOUNTS     (ACCOUNT_ID, CUSTOMER_ID, ACCOUNT_TYPE, OPENED_AT, STATUS)
TRANSACTIONS (TXN_ID, FROM_ACCOUNT_ID, TO_ACCOUNT_ID, AMOUNT, CURRENCY, CHANNEL, COUNTERPARTY_COUNTRY, TXN_TS)
             -- FROM/TO = internal ACCOUNT_ID, or NULL for the external side
             -- cash deposit = FROM NULL, TO the account, CHANNEL 'CASH'
             -- CHANNEL in (CASH, WIRE, ACH, CARD, INTERNAL)
             -- COUNTERPARTY_COUNTRY = ISO-2 code (US, GB, IR...), matches FATF_JURISDICTIONS
TXN_LABELS   (TXN_ID, TYPOLOGY)   -- ground truth; detection rules must never read this table
ALERTS       -- see sql/10_alerts_and_cycle_proc.sql
```

Seeded typologies (~30 cases each): `STRUCTURING`, `VELOCITY`, `LAYERING`, `GEO_RISK`, `ROUND_TRIP`.

## Detection rules → policy clauses

| Rule | Policy clause | Implementation |
|---|---|---|
| STRUCTURING | §3.3 | SQL: ≥3 cash deposits $8K–$10K in 7 days, total > $10K |
| VELOCITY | §4.1 | SQL: day value > 5× trailing 90-day daily average, min $25K |
| GEO_RISK | §6.2 | SQL: any FATF black-list wire; grey-list wires > $50K in 30 days |
| ROUND_TRIP_CYCLE | §5.2 | `detection/cycles.py`: time-ordered DFS, 2–4 hops, 7 days, ≥80% conserved |
| (severity bump) | §7.2 | `ALERT_QUEUE` view: high-risk customers raised one level |

## Corpus sources (Cortex Search)

| File | Source |
|---|---|
| `internal_aml_policy.md` / `.pdf` | Written for this demo (fictional bank); the rules above cite it. PDF is what gets indexed |
| `fincen_sar_narrative_guidance.pdf` | FinCEN, *Guidance on Preparing a Complete & Sufficient SAR Narrative* |
| `fincen_sar_filing_instructions.pdf` | FinCEN, SAR Electronic Filing Instructions |
| *download by hand (site blocks scripts)* | FATF Recommendations — fatf-gafi.org → Recommendations |
| *download by hand* | FATF high-risk & monitored jurisdictions (current list) — fatf-gafi.org |
| *download by hand* | FATF *Professional Money Laundering* / layering typology report |

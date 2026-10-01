# Risk Copilot — Fraud, AML & Regulatory Intelligence on Snowflake

Snowflake CoCo CLI Hackathon 2026 (GCC Edition) — Problem Statement #1.

Signal → evidence → documented finding → SAR draft, for AML analysts at GCC risk-ops
teams serving global banks. **Detection is deterministic (SQL + Snowpark); the LLM only
explains and drafts, never decides what is fraud.**

## Layout

| Path | What | Owner |
|---|---|---|
| `data/` | Synthetic data generator + load into Snowflake | Udith |
| `detection/cycles.py` | Layering / round-trip cycle detection (Snowpark proc) | Pranav |
| `sql/10_alerts_and_cycle_proc.sql` | Shared `ALERTS` table + registers the cycle proc | Pranav |
| `corpus/` | Documents indexed by Cortex Search (see Sources) | Pranav |

## Data contract (all detection, the semantic model and the app depend on these names)

All amounts in **USD** (policy thresholds are BSA/FinCEN: $10K CTR etc.).

```
CUSTOMERS    (CUSTOMER_ID, NAME, RISK_RATING, OCCUPATION, DECLARED_MONTHLY_INCOME, COUNTRY, ONBOARDED_AT)
ACCOUNTS     (ACCOUNT_ID, CUSTOMER_ID, ACCOUNT_TYPE, OPENED_AT, STATUS)
TRANSACTIONS (TXN_ID, FROM_ACCOUNT_ID, TO_ACCOUNT_ID, AMOUNT, CURRENCY, CHANNEL, COUNTERPARTY_COUNTRY, TXN_TS)
             -- TO_ACCOUNT_ID = internal account, or NULL for external counterparties
             -- CHANNEL in (CASH, WIRE, ACH, CARD, INTERNAL)
TXN_LABELS   (TXN_ID, TYPOLOGY)   -- ground truth; detection rules must never read this table
ALERTS       -- see sql/10_alerts_and_cycle_proc.sql
```

Seeded typologies (~30 cases each): `STRUCTURING`, `VELOCITY`, `LAYERING`, `GEO_RISK`, `ROUND_TRIP`.

## Detection rules → policy clauses

| Rule | Policy clause | Implementation |
|---|---|---|
| STRUCTURING | §3.3 | SQL (todo) |
| VELOCITY | §4.1 | SQL (todo) |
| ROUND_TRIP_CYCLE | §5.2 | `detection/cycles.py` — time-ordered DFS, 2–4 hops, 7 days, ≥80% conserved |
| GEO_RISK | §6.2 | SQL + FATF list (todo) |

```
python3 detection/cycles.py     # local self-check
```

On 100K random transactions with 30 planted cycles: recall 1.00, precision 1.00, <1s
(random noise is easy; re-measure on the real synthetic set).

## Corpus sources (Cortex Search)

| File | Source |
|---|---|
| `internal_aml_policy.md` | Written for this demo (fictional bank); the rules above cite it |
| `fincen_sar_narrative_guidance.pdf` | FinCEN, *Guidance on Preparing a Complete & Sufficient SAR Narrative* |
| `fincen_sar_filing_instructions.pdf` | FinCEN, SAR Electronic Filing Instructions |
| *download by hand (site blocks scripts)* | FATF Recommendations — fatf-gafi.org → Recommendations |
| *download by hand* | FATF high-risk & monitored jurisdictions (current list) — fatf-gafi.org |
| *download by hand* | FATF *Professional Money Laundering* / layering typology report |

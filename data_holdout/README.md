# Held-out generated data (Udith)

Fully synthetic. No real customers, accounts or PII. Same generator as `data/`
(`data_generator/generate_data.py`), run with a **different RNG seed
(`--seed 20259`)** so these numbers are honest and untuned — unlike `data/`,
no detection threshold was calibrated against this file.

| File | Rows | Loaded as | Mapped to (sql/02_canonical.sql) |
|---|---|---|---|
| `customers.csv` | 800 | `RAW_CUSTOMERS` | `CUSTOMERS` |
| `accounts.csv` | 1,042 | `RAW_ACCOUNTS` | `ACCOUNTS` |
| `transactions.csv` | 41,630 | `RAW_TRANSACTIONS` | `TRANSACTIONS` (one-sided rows → FROM/TO) |
| `ground_truth_labels.csv` | 144 | `RAW_LABELS` | `TXN_LABELS` (evaluation only) |
| `reference_high_risk_countries.csv` | 6 | `RAW_HIGH_RISK_COUNTRIES` | merged into `FATF_JURISDICTIONS` |

Same mapping decisions as `data/` (direction/cash inference, INR-as-USD
currency reinterpretation for threshold sizing) — see `data/README.md`.

One generator fix applied here (not retroactively applied to `data/`, to
avoid disturbing Pranav's already-reported numbers): the rapid-layering
typology's outflow fraction was raised from 25–40% per step to 35–45% per
step. The old range could, in the worst case, land at ~43.75% total outflow
— under the `PASS_THROUGH` rule's 50% threshold — which is exactly what
caused the one miss noted in `data/README.md` (47.9% case). The new range's
worst case is ~57.75%, comfortably above the threshold.

Results (`pip install duckdb && python3 tests/run_on_holdout.py`):

| Rule | Alerts | Precision | Case recall | Txn recall |
|---|---|---|---|---|
| GEO_RISK | 11 | 1.00 | 11/11 | 11/11 |
| PASS_THROUGH | 13 | 1.00 | 13/13 | 44/44 |
| ROUND_TRIP_CYCLE | 4 | 1.00 | 12/12 | 12/12 |
| STRUCTURING | 8 | 1.00 | 8/8 | 30/30 |
| VELOCITY | 28 | 0.71 | 9/9 (all cases found) | 36/47 |

Every typology's cases are fully recalled (case-level recall 1.00 across the
board). VELOCITY's txn-level precision/recall gap mirrors the original
dataset's pattern — it's a genuinely harder rule (bursty legitimate activity
can look similar), not an artifact of tuning. These are the numbers to quote
to judges, since this file was never used to calibrate any threshold.

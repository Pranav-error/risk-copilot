# Generated data (Udith)

Fully synthetic. No real customers, accounts or PII.

| File | Rows | Loaded as | Mapped to (sql/02_canonical.sql) |
|---|---|---|---|
| `customers.csv` | 800 | `RAW_CUSTOMERS` | `CUSTOMERS` |
| `accounts.csv` | 1,043 | `RAW_ACCOUNTS` | `ACCOUNTS` |
| `transactions.csv` | 41,492 | `RAW_TRANSACTIONS` | `TRANSACTIONS` (one-sided rows → FROM/TO) |
| `ground_truth_labels.csv` | 168 | `RAW_LABELS` | `TXN_LABELS` (evaluation only) |
| `reference_high_risk_countries.csv` | 6 | `RAW_HIGH_RISK_COUNTRIES` | merged into `FATF_JURISDICTIONS` |

Planted typologies: STRUCTURING (12 accounts), VELOCITY_ANOMALY (12), RAPID_LAYERING (11),
ROUND_TRIPPING (4 three-hop cycles, 12 accounts), GEOGRAPHIC_RISK (6 wires).

Mapping decisions:
- **Direction:** `deposit` or a description starting "incoming" is an inflow; everything else is outgoing.
- **Cash:** description "cash …", or deposit/withdrawal at ATM/branch.
- **Currency:** the file says INR but typologies are sized for US BSA thresholds
  (structuring at 9,000–9,900 under 10,000), so amounts are read as USD.

Results (`python3 tests/run_on_data.py`): every scheme caught except one layering account that
passed on 47.9% of its inflow (rule threshold 50%). Thresholds were calibrated on this file, so
quote numbers from a second, differently-seeded file that nobody tuned against.

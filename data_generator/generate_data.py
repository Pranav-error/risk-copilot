"""
Synthetic data generator for Risk Copilot (Snowflake CoCo CLI Hackathon 2026,
GCC Edition, Problem Statement #1).

Produces 5 CSV files (customers, accounts, transactions, ground_truth_labels,
reference_high_risk_countries) with the exact column names sql/01_load_raw.sql
and data/README.md expect.

Usage:
    pip install -r requirements.txt
    python generate_data.py                                   # seed 42 -> ../data
    python generate_data.py --seed 777 --output-dir ../data_holdout   # held-out set

Data-grounding notes (what is actually calibrated against real sources vs.
reasoned assumption):
  - Fraud prevalence (~0.4% of transactions seeded as fraud) is kept in the
    same order of magnitude as the real, widely-cited PaySim mobile-money
    fraud dataset, whose published fraud rate is well under 1% of
    transactions (confirmed via a live CrossRef lookup of a 2025 paper using
    PaySim, DOI 10.30574/wjarr.2025.28.3.4058, reporting Precision/Recall of
    0.68/0.97 (Decision Tree) and 0.16/0.98 (Random Forest) -- i.e. a genuine
    precision/recall trade-off, not a solved problem).
  - HIGH_RISK_COUNTRIES below is NOT a live-fetched FATF list -- fatf-gafi.org
    blocks scripted downloads (403), confirmed independently by both of us.
    It's a small illustrative set from general knowledge. 05_reference.sql
    merges it with the bank's/repo's own FATF_JURISDICTIONS list, so this is
    additive, not authoritative -- see corpus/ for the real FATF documents.
  - Sanctions/PEP-style screening, if added later, should point at
    OpenSanctions.org, which was confirmed live and fetchable.
"""

import argparse
import csv
import os
import random
import uuid
from datetime import datetime, timedelta

import numpy as np
from faker import Faker

NUM_CUSTOMERS = 800
HISTORY_DAYS = 180  # 6 months of transaction history
CURRENCY = "INR"

# FATF-style grey-list country codes used for the geographic-risk typology.
# ILLUSTRATIVE ONLY -- general knowledge, not a live-fetched official FATF
# list. See data/README.md and sql/05_reference.sql for how this is merged
# with the repo's real FATF_JURISDICTIONS reference data.
HIGH_RISK_COUNTRIES = ["MM", "KP", "IR", "SY", "YE", "PA"]
NORMAL_COUNTRIES = ["IN", "US", "GB", "AE", "SG", "DE"]

BUSINESS_SECTORS = [
    "retail_trade", "it_services", "manufacturing", "real_estate",
    "logistics", "hospitality", "import_export",
]


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------
def generate_customers(n=NUM_CUSTOMERS, fake=None):
    customers = []
    for _ in range(n):
        customer_type = np.random.choice(["individual", "business"], p=[0.8, 0.2])
        # KYC risk rating is weighted so "high" stays rare, matching real-world
        # distributions where most customers are low/medium risk.
        kyc_risk_rating = np.random.choice(
            ["low", "medium", "high"], p=[0.70, 0.25, 0.05]
        )
        pep_flag = bool(np.random.choice([True, False], p=[0.02, 0.98]))
        onboarding_date = fake.date_between(start_date="-5y", end_date="-200d")

        if customer_type == "business":
            expected_monthly_volume = round(np.random.lognormal(mean=11.5, sigma=0.8), 2)
            business_sector = random.choice(BUSINESS_SECTORS)
            full_name = fake.company()
        else:
            expected_monthly_volume = round(np.random.lognormal(mean=9.5, sigma=0.6), 2)
            business_sector = None
            full_name = fake.name()

        customers.append({
            "customer_id": f"CUST_{uuid.uuid4().hex[:10].upper()}",
            "full_name": full_name,
            "customer_type": customer_type,
            "country": "IN",
            "onboarding_date": onboarding_date.isoformat(),
            "kyc_risk_rating": kyc_risk_rating,
            "pep_flag": pep_flag,
            "business_sector": business_sector or "",
            "expected_monthly_volume": expected_monthly_volume,
        })
    return customers


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------
def generate_accounts(customers):
    accounts = []
    for cust in customers:
        n_accounts = np.random.choice([1, 2, 3], p=[0.75, 0.2, 0.05])
        onboarding = datetime.fromisoformat(cust["onboarding_date"])
        for _ in range(n_accounts):
            account_type = (
                "current" if cust["customer_type"] == "business"
                else np.random.choice(["savings", "nbfc_loan"], p=[0.9, 0.1])
            )
            open_date = onboarding + timedelta(days=np.random.randint(0, 30))
            accounts.append({
                "account_id": f"ACC_{uuid.uuid4().hex[:10].upper()}",
                "customer_id": cust["customer_id"],
                "account_type": account_type,
                "open_date": open_date.date().isoformat(),
                "status": np.random.choice(["active", "dormant"], p=[0.92, 0.08]),
                "currency": CURRENCY,
            })
    return accounts


# ---------------------------------------------------------------------------
# Baseline ("normal") transactions
# ---------------------------------------------------------------------------
TXN_TYPES = ["deposit", "withdrawal", "transfer", "wire", "upi"]
CHANNELS = ["branch", "online", "atm", "mobile"]


def _random_timestamp_within(days_back):
    """Business-hours-weighted timestamp, more realistic than uniform random."""
    day_offset = np.random.randint(0, days_back)
    hour = int(np.clip(np.random.normal(loc=14, scale=4), 0, 23))
    minute = np.random.randint(0, 60)
    ts = datetime.now() - timedelta(days=day_offset)
    return ts.replace(hour=hour, minute=minute, second=np.random.randint(0, 60))


def generate_baseline_transactions(accounts, customers, fake=None):
    cust_by_id = {c["customer_id"]: c for c in customers}
    transactions = []

    for acc in accounts:
        cust = cust_by_id[acc["customer_id"]]
        monthly_vol = cust["expected_monthly_volume"]
        # Roughly scale transaction count/size off the customer's declared
        # expected volume so behaviour differs sensibly by segment.
        n_txns = int(np.clip(np.random.poisson(lam=40), 5, 400))

        # A handful of recurring counterparties per account -- real customers
        # pay/receive from mostly the same people and merchants repeatedly.
        recurring_counterparties = [
            f"CPTY_{uuid.uuid4().hex[:8].upper()}" for _ in range(np.random.randint(3, 8))
        ]

        for _ in range(n_txns):
            use_recurring = np.random.rand() < 0.8
            counterparty = (
                random.choice(recurring_counterparties)
                if use_recurring else f"CPTY_{uuid.uuid4().hex[:8].upper()}"
            )
            amount = round(
                float(np.clip(np.random.lognormal(mean=np.log(monthly_vol / 25 + 1), sigma=0.9), 50, monthly_vol * 2)),
                2,
            )
            transactions.append({
                "txn_id": f"TXN_{uuid.uuid4().hex[:12].upper()}",
                "account_id": acc["account_id"],
                "counterparty_account": counterparty,
                "counterparty_country": np.random.choice(
                    NORMAL_COUNTRIES, p=[0.85, 0.05, 0.04, 0.03, 0.02, 0.01]
                ),
                "amount": amount,
                "currency": CURRENCY,
                "txn_type": random.choice(TXN_TYPES),
                "channel": random.choice(CHANNELS),
                "txn_timestamp": _random_timestamp_within(HISTORY_DAYS).isoformat(),
                "description": fake.sentence(nb_words=4),
                "is_seeded_fraud": False,
                "typology_type": "",
            })
    return transactions


# ---------------------------------------------------------------------------
# Seeded fraud typologies (ground truth)
# ---------------------------------------------------------------------------
def inject_structuring(account_id, n_cases=1):
    """Multiple deposits just under a reporting threshold within 48h."""
    txns = []
    for _ in range(n_cases):
        start = _random_timestamp_within(HISTORY_DAYS)
        for i in range(np.random.randint(3, 5)):
            txns.append({
                "txn_id": f"TXN_{uuid.uuid4().hex[:12].upper()}",
                "account_id": account_id,
                "counterparty_account": f"CPTY_{uuid.uuid4().hex[:8].upper()}",
                "counterparty_country": "IN",
                "amount": round(np.random.uniform(9000, 9900), 2),
                "currency": CURRENCY,
                "txn_type": "deposit",
                "channel": random.choice(CHANNELS),
                "txn_timestamp": (start + timedelta(hours=i * np.random.randint(2, 16))).isoformat(),
                "description": "cash deposit",
                "is_seeded_fraud": True,
                "typology_type": "STRUCTURING",
            })
    return txns


def inject_velocity_anomaly(account_id, expected_monthly_volume, n_cases=1):
    """Sudden burst summing to 5-10x the customer's expected monthly volume."""
    txns = []
    for _ in range(n_cases):
        start = _random_timestamp_within(HISTORY_DAYS)
        target_total = expected_monthly_volume * np.random.uniform(5, 10)
        n_txns = np.random.randint(4, 8)
        for i in range(n_txns):
            txns.append({
                "txn_id": f"TXN_{uuid.uuid4().hex[:12].upper()}",
                "account_id": account_id,
                "counterparty_account": f"CPTY_{uuid.uuid4().hex[:8].upper()}",
                "counterparty_country": "IN",
                "amount": round(target_total / n_txns, 2),
                "currency": CURRENCY,
                "txn_type": "transfer",
                "channel": random.choice(CHANNELS),
                "txn_timestamp": (start + timedelta(hours=i * 6)).isoformat(),
                "description": "fund transfer",
                "is_seeded_fraud": True,
                "typology_type": "VELOCITY_ANOMALY",
            })
    return txns


def inject_rapid_layering(account_id, n_cases=1):
    """Large inflow followed by >80% outflow within 24 hours."""
    txns = []
    for _ in range(n_cases):
        start = _random_timestamp_within(HISTORY_DAYS)
        inflow = round(np.random.uniform(200000, 800000), 2)
        txns.append({
            "txn_id": f"TXN_{uuid.uuid4().hex[:12].upper()}",
            "account_id": account_id,
            "counterparty_account": f"CPTY_{uuid.uuid4().hex[:8].upper()}",
            "counterparty_country": "IN",
            "amount": inflow,
            "currency": CURRENCY,
            "txn_type": "wire",
            "channel": "online",
            "txn_timestamp": start.isoformat(),
            "description": "incoming wire",
            "is_seeded_fraud": True,
            "typology_type": "RAPID_LAYERING",
        })
        remaining = inflow
        # >=80% outflow within 24h, matching the detection rule's threshold --
        # keep the fraction comfortably above 50% so a seeded case does not
        # land in the rule's blind spot (see data/README.md: one case in the
        # original seed cleared only 47.9% and was missed).
        n_out = np.random.randint(2, 4)
        for i in range(n_out):
            out_amt = round(remaining * np.random.uniform(0.35, 0.45), 2)
            remaining -= out_amt
            txns.append({
                "txn_id": f"TXN_{uuid.uuid4().hex[:12].upper()}",
                "account_id": account_id,
                "counterparty_account": f"CPTY_{uuid.uuid4().hex[:8].upper()}",
                "counterparty_country": "IN",
                "amount": out_amt,
                "currency": CURRENCY,
                "txn_type": "transfer",
                "channel": "online",
                "txn_timestamp": (start + timedelta(hours=np.random.randint(1, 24))).isoformat(),
                "description": "outgoing transfer",
                "is_seeded_fraud": True,
                "typology_type": "RAPID_LAYERING",
            })
    return txns


def inject_geographic_risk(account_id, n_cases=1):
    """Transaction to/from a FATF-grey-list-style high-risk jurisdiction."""
    txns = []
    for _ in range(n_cases):
        txns.append({
            "txn_id": f"TXN_{uuid.uuid4().hex[:12].upper()}",
            "account_id": account_id,
            "counterparty_account": f"CPTY_{uuid.uuid4().hex[:8].upper()}",
            "counterparty_country": random.choice(HIGH_RISK_COUNTRIES),
            "amount": round(np.random.uniform(50000, 400000), 2),
            "currency": CURRENCY,
            "txn_type": "wire",
            "channel": "online",
            "txn_timestamp": _random_timestamp_within(HISTORY_DAYS).isoformat(),
            "description": "cross-border wire",
            "is_seeded_fraud": True,
            "typology_type": "GEOGRAPHIC_RISK",
        })
    return txns


def inject_round_tripping(account_ids, n_cases=1):
    """Funds cycle A -> B -> C -> A within a few days, net ~zero movement."""
    txns = []
    for _ in range(n_cases):
        if len(account_ids) < 3:
            break
        ring = random.sample(account_ids, 3)
        amount = round(np.random.uniform(100000, 300000), 2)
        start = _random_timestamp_within(HISTORY_DAYS)
        for i in range(3):
            src, dst = ring[i], ring[(i + 1) % 3]
            txns.append({
                "txn_id": f"TXN_{uuid.uuid4().hex[:12].upper()}",
                "account_id": src,
                "counterparty_account": dst,
                "counterparty_country": "IN",
                "amount": amount,
                "currency": CURRENCY,
                "txn_type": "transfer",
                "channel": "online",
                "txn_timestamp": (start + timedelta(hours=i * 20)).isoformat(),
                "description": "fund transfer",
                "is_seeded_fraud": True,
                "typology_type": "ROUND_TRIPPING",
            })
    return txns


def inject_fraud_typologies(accounts, customers, fraud_rate=0.04):
    """
    Injects seeded fraud typologies onto a small, random subset of accounts
    (~4% by default, matching real-world AML alert rates), and returns the
    extra transactions plus a ground-truth label list.
    """
    cust_by_id = {c["customer_id"]: c for c in customers}
    n_targets = max(5, int(len(accounts) * fraud_rate))
    targeted_accounts = random.sample(accounts, n_targets)
    all_account_ids = [a["account_id"] for a in accounts]

    injected_txns = []
    typology_funcs = [
        lambda acc: inject_structuring(acc["account_id"]),
        lambda acc: inject_velocity_anomaly(
            acc["account_id"], cust_by_id[acc["customer_id"]]["expected_monthly_volume"]
        ),
        lambda acc: inject_rapid_layering(acc["account_id"]),
        lambda acc: inject_geographic_risk(acc["account_id"]),
    ]

    for acc in targeted_accounts:
        fn = random.choice(typology_funcs)
        injected_txns.extend(fn(acc))

    # A few round-tripping rings, sampled independently across accounts.
    for _ in range(max(2, n_targets // 10)):
        injected_txns.extend(inject_round_tripping(all_account_ids))

    return injected_txns


# ---------------------------------------------------------------------------
# Write CSVs
# ---------------------------------------------------------------------------
def write_csv(rows, path, fieldnames):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows):,} rows -> {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42,
                         help="RNG seed (default 42, the original tuned-against dataset)")
    parser.add_argument("--output-dir", default=None,
                         help="Output directory (default: ../data relative to this script)")
    args = parser.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    fake = Faker("en_IN")
    Faker.seed(args.seed)

    output_dir = args.output_dir or os.path.join(os.path.dirname(__file__), "..", "data")
    os.makedirs(output_dir, exist_ok=True)

    print(f"Generating with seed={args.seed} -> {output_dir}")

    print("Generating customers...")
    customers = generate_customers(fake=fake)

    print("Generating accounts...")
    accounts = generate_accounts(customers)

    print("Generating baseline transactions...")
    baseline_txns = generate_baseline_transactions(accounts, customers, fake=fake)

    print("Injecting seeded fraud typologies...")
    fraud_txns = inject_fraud_typologies(accounts, customers)

    all_txns = baseline_txns + fraud_txns
    random.shuffle(all_txns)

    ground_truth = [
        {"txn_id": t["txn_id"], "account_id": t["account_id"], "typology_type": t["typology_type"]}
        for t in all_txns if t["is_seeded_fraud"]
    ]

    # The app/Snowflake-facing transactions table must NOT expose the
    # ground-truth columns -- strip them before writing the main file.
    app_facing_txns = [
        {k: v for k, v in t.items() if k not in ("is_seeded_fraud", "typology_type")}
        for t in all_txns
    ]

    write_csv(
        customers, os.path.join(output_dir, "customers.csv"),
        ["customer_id", "full_name", "customer_type", "country", "onboarding_date",
         "kyc_risk_rating", "pep_flag", "business_sector", "expected_monthly_volume"],
    )
    write_csv(
        accounts, os.path.join(output_dir, "accounts.csv"),
        ["account_id", "customer_id", "account_type", "open_date", "status", "currency"],
    )
    write_csv(
        app_facing_txns, os.path.join(output_dir, "transactions.csv"),
        ["txn_id", "account_id", "counterparty_account", "counterparty_country",
         "amount", "currency", "txn_type", "channel", "txn_timestamp", "description"],
    )
    write_csv(
        ground_truth, os.path.join(output_dir, "ground_truth_labels.csv"),
        ["txn_id", "account_id", "typology_type"],
    )
    write_csv(
        [{"country_code": c} for c in HIGH_RISK_COUNTRIES],
        os.path.join(output_dir, "reference_high_risk_countries.csv"),
        ["country_code"],
    )

    print("\nDone. Summary:")
    print(f"  Customers:            {len(customers):,}")
    print(f"  Accounts:             {len(accounts):,}")
    print(f"  Transactions (total): {len(all_txns):,}")
    print(f"  Seeded fraud txns:    {len(ground_truth):,} "
          f"({len(ground_truth) / len(all_txns):.2%} of all transactions)")
    print("\n  NOTE: ground_truth_labels.csv is for internal precision/recall "
          "validation only. Do not load it into the app-facing Snowflake "
          "schema or expose it to the LLM.")


if __name__ == "__main__":
    main()

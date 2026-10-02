"""Detection rules on planted typologies, no Snowflake needed. Checks the logic itself;
tests/run_on_data.py measures the real generated data.

    pip install duckdb && python3 tests/test_rules_duckdb.py
"""
import random
from datetime import datetime, timedelta

from duck import check_rerun_stable, connect, detect_and_evaluate, run_sql_file

random.seed(7)
T0 = datetime(2026, 1, 1)
db = connect()
db.execute("""
CREATE TABLE CUSTOMERS (CUSTOMER_ID VARCHAR, RISK_RATING VARCHAR);
CREATE TABLE ACCOUNTS (ACCOUNT_ID VARCHAR, CUSTOMER_ID VARCHAR);
CREATE TABLE TRANSACTIONS (TXN_ID VARCHAR, FROM_ACCOUNT_ID VARCHAR, TO_ACCOUNT_ID VARCHAR,
    AMOUNT DOUBLE, CURRENCY VARCHAR, CHANNEL VARCHAR, COUNTERPARTY_COUNTRY VARCHAR, TXN_TS TIMESTAMP);
CREATE TABLE TXN_LABELS (TXN_ID VARCHAR, ACCOUNT_ID VARCHAR, TYPOLOGY VARCHAR);
CREATE TABLE RAW_HIGH_RISK_COUNTRIES (country_code VARCHAR);
""")

accts = [f"AC{i:05d}" for i in range(2000)]
db.executemany("INSERT INTO CUSTOMERS VALUES (?, ?)",
               [(f"CU{i:05d}", "HIGH" if i % 25 == 0 else "LOW") for i in range(2000)])
db.executemany("INSERT INTO ACCOUNTS VALUES (?, ?)", [(a, f"CU{i:05d}") for i, a in enumerate(accts)])

txns, labels, n = [], [], 0


def tx(frm, to, amt, ch, ts, country="US", label=None):
    global n
    n += 1
    tid = f"T{n:07d}"
    txns.append((tid, frm, to, round(amt, 2), "USD", ch, country, ts))
    if label:
        labels.append((tid, frm or to, label))


# background: ordinary activity, steady per account so velocity has a baseline
for a in accts:
    base = random.lognormvariate(6.5, 0.8)
    for _ in range(random.randint(10, 25)):
        ts = T0 + timedelta(minutes=random.randint(0, 60 * 24 * 180))
        ch = random.choice(["ACH", "CARD", "CARD", "INTERNAL", "WIRE", "CASH"])
        to = random.choice(accts) if ch == "INTERNAL" else None
        tx(a, to, base * random.uniform(0.5, 1.5), ch, ts, random.choice(["US"] * 30 + ["GB", "IN", "AE"]))

bad = iter(random.sample(accts, 200))
for _ in range(20):  # structuring: 3-5 cash deposits just under $10k within a week
    a, ts = next(bad), T0 + timedelta(days=random.randint(10, 160))
    for _ in range(random.randint(3, 5)):
        ts += timedelta(hours=random.randint(4, 36))
        tx(None, a, random.uniform(8200, 9950), "CASH", ts, label="STRUCTURING")
for _ in range(20):  # velocity: a burst of 4-6 payments in a day, far above normal
    a, ts = next(bad), T0 + timedelta(days=random.randint(100, 170))
    for _ in range(random.randint(4, 6)):
        tx(a, None, random.uniform(5000, 20000), "WIRE", ts + timedelta(minutes=random.randint(0, 1200)), label="VELOCITY")
for _ in range(20):  # pass-through: big inflow, most of it out again within a day or two
    a, ts = next(bad), T0 + timedelta(days=random.randint(0, 170))
    amt = random.uniform(100000, 500000)
    tx(None, a, amt, "WIRE", ts, label="LAYERING")
    for share in (0.3, 0.25, 0.2):
        ts += timedelta(hours=random.randint(1, 15))
        tx(a, None, amt * share, "ACH", ts, label="LAYERING")
for _ in range(10):  # geo: black-list wire
    tx(next(bad), None, random.uniform(5000, 90000), "WIRE",
       T0 + timedelta(days=random.randint(0, 170)), random.choice(["IR", "KP", "MM"]), label="GEO_RISK")
for _ in range(10):  # geo: grey-list wires adding up past $50k in a month
    a, ts = next(bad), T0 + timedelta(days=random.randint(0, 140))
    for _ in range(3):
        ts += timedelta(days=random.randint(1, 8))
        tx(a, None, random.uniform(18000, 30000), "WIRE", ts, random.choice(["VN", "NG", "HT"]), label="GEO_RISK")
for _ in range(20):  # round trip / layering cycles
    hops = random.choice([2, 3, 4])
    path = [next(bad) for _ in range(hops)]
    amt, ts = random.uniform(30000, 300000), T0 + timedelta(days=random.randint(0, 170))
    for h in range(hops):
        ts += timedelta(hours=random.randint(2, 30))
        tx(path[h], path[(h + 1) % hops], amt, "INTERNAL", ts, label="ROUND_TRIP")
        amt *= random.uniform(0.95, 0.995)

db.executemany("INSERT INTO TRANSACTIONS VALUES (?,?,?,?,?,?,?,?)", txns)
db.executemany("INSERT INTO TXN_LABELS VALUES (?, ?, ?)", labels)
run_sql_file(db, "05_reference.sql")
print(f"{len(txns)} txns, {len(labels)} labelled")
metrics = detect_and_evaluate(db)

cols = [d[0] for d in db.execute("SELECT * FROM RULE_METRICS LIMIT 0").description]
for row in metrics:
    m = dict(zip(cols, row))
    assert m["CASE_RECALL"] == 1.0, m
    assert m["PRECISION"] >= 0.95, m

# Reruns must not renumber alerts or reset analyst decisions, or FINDINGS/SARs lose their alert.
check_rerun_stable(db)
# python key (cycles proc) == SQL key formula (rules), or a cycle could be inserted twice
mismatch = db.execute("""SELECT COUNT(*) FROM ALERTS WHERE ALERT_KEY <>
    md5(RULE || '|' || array_to_string(list_sort(TXN_IDS), ','))""").fetchone()[0]
assert mismatch == 0, mismatch

# §7.2: high-risk customers' alerts are escalated in the view, never in ALERTS itself
bumped = db.execute("SELECT COUNT(*) FROM ALERT_QUEUE WHERE RISK_RATING = 'HIGH' AND SEVERITY = 'MEDIUM' "
                    "AND EFFECTIVE_SEVERITY <> 'HIGH'").fetchone()[0]
assert bumped == 0, bumped
print("ok")

"""Run the real detection SQL on DuckDB against planted typologies, no Snowflake needed.

    pip install duckdb && python3 tests/test_rules_duckdb.py

Only Snowflake-only syntax is translated (OBJECT_CONSTRUCT, FLATTEN, VALUES-as-view);
the rule logic itself is the exact text in sql/.
"""
import random
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "detection"))
from cycles import find_cycles, to_alert  # noqa: E402

random.seed(7)
T0 = datetime(2026, 1, 1)
db = duckdb.connect()


def run_sql_file(name, skip=()):
    text = re.sub(r"--[^\n]*", "", (ROOT / "sql" / name).read_text())
    text = text.replace("OBJECT_CONSTRUCT(", "json_object(")
    for stmt in filter(str.strip, text.split(";")):
        if not any(s in stmt for s in skip):
            db.execute(stmt)


db.execute("""
CREATE SEQUENCE alert_seq;
CREATE TABLE CUSTOMERS (CUSTOMER_ID VARCHAR, RISK_RATING VARCHAR);
CREATE TABLE ACCOUNTS (ACCOUNT_ID VARCHAR, CUSTOMER_ID VARCHAR);
CREATE TABLE TRANSACTIONS (TXN_ID VARCHAR, FROM_ACCOUNT_ID VARCHAR, TO_ACCOUNT_ID VARCHAR,
    AMOUNT DOUBLE, CURRENCY VARCHAR, CHANNEL VARCHAR, COUNTERPARTY_COUNTRY VARCHAR, TXN_TS TIMESTAMP);
CREATE TABLE TXN_LABELS (TXN_ID VARCHAR, TYPOLOGY VARCHAR);
CREATE TABLE ALERTS (ALERT_ID INTEGER DEFAULT nextval('alert_seq'), RULE VARCHAR, ACCOUNT_ID VARCHAR,
    TXN_IDS VARCHAR[], SEVERITY VARCHAR, EVIDENCE JSON, STATUS VARCHAR DEFAULT 'OPEN',
    CREATED_AT TIMESTAMP DEFAULT current_timestamp);
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
        labels.append((tid, label))


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
for _ in range(20):  # velocity: one day far above the account's normal level
    a, ts = next(bad), T0 + timedelta(days=random.randint(100, 170))
    for _ in range(random.randint(3, 6)):
        tx(a, None, random.uniform(15000, 40000), "WIRE", ts + timedelta(minutes=random.randint(0, 600)), label="VELOCITY")
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
        tx(path[h], path[(h + 1) % hops], amt, "INTERNAL", ts, label="ROUND_TRIP" if hops == 2 else "LAYERING")
        amt *= random.uniform(0.95, 0.995)

db.executemany("INSERT INTO TRANSACTIONS VALUES (?,?,?,?,?,?,?,?)", txns)
db.executemany("INSERT INTO TXN_LABELS VALUES (?, ?)", labels)

run_sql_file("05_reference.sql")
run_sql_file("20_detection_rules.sql", skip=("ALERT_TXNS AS",))

# cycle rule: same python the Snowpark proc runs
rows = db.execute("SELECT TXN_ID, FROM_ACCOUNT_ID, TO_ACCOUNT_ID, AMOUNT, TXN_TS FROM TRANSACTIONS "
                  "WHERE FROM_ACCOUNT_ID IS NOT NULL AND TO_ACCOUNT_ID IS NOT NULL").fetchall()
for a in map(to_alert, find_cycles(rows)):
    db.execute("INSERT INTO ALERTS (RULE, ACCOUNT_ID, TXN_IDS, SEVERITY, EVIDENCE) VALUES (?,?,?,?,?)",
               [a["RULE"], a["ACCOUNT_ID"], a["TXN_IDS"], a["SEVERITY"], str(a["EVIDENCE"]).replace("'", '"')])

db.execute("CREATE VIEW ALERT_TXNS AS SELECT ALERT_ID, RULE, unnest(TXN_IDS) AS TXN_ID FROM ALERTS")
db.execute("CREATE VIEW RULE_TYPOLOGY AS SELECT * FROM (VALUES ('STRUCTURING','STRUCTURING'),"
           "('VELOCITY','VELOCITY'),('GEO_RISK','GEO_RISK'),('ROUND_TRIP_CYCLE','ROUND_TRIP'),"
           "('ROUND_TRIP_CYCLE','LAYERING')) v(RULE, TYPOLOGY)")
run_sql_file("30_evaluate.sql", skip=("ALERT_TXNS AS", "RULE_TYPOLOGY AS"))

print(f"{len(txns)} txns, {len(labels)} labelled")
metrics = db.execute("SELECT * FROM RULE_METRICS").fetchall()
cols = [d[0] for d in db.description]
print(" | ".join(cols))
for r in metrics:
    print(" | ".join(str(v) for v in r))

for rule, alerts, true_alerts, precision, typ_precision, labelled, caught, recall in metrics:
    assert recall is not None and recall >= 0.9, f"{rule} recall {recall}"
    assert precision is not None and precision >= 0.95, f"{rule} precision {precision}"
# §7.2: high-risk customers' alerts are escalated in the view, never in ALERTS itself
bumped = db.execute("SELECT COUNT(*) FROM ALERT_QUEUE WHERE RISK_RATING = 'HIGH' AND SEVERITY = 'MEDIUM' "
                    "AND EFFECTIVE_SEVERITY <> 'HIGH'").fetchone()[0]
assert bumped == 0, bumped
print("ok")

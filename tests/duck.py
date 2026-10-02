"""Run the repo's Snowflake SQL on DuckDB. Only Snowflake-only syntax is translated;
the logic is the exact text in sql/."""
import re
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "detection"))
from cycles import alert_key, find_cycles, to_alert  # noqa: E402

SNOWFLAKE_ONLY = ("ALERT_TXNS AS", "RULE_TYPOLOGY AS", "CREATE STAGE", "FILE FORMAT", "COPY INTO")


def connect():
    db = duckdb.connect()
    db.execute("""
    CREATE SEQUENCE alert_seq;
    CREATE TABLE ALERTS (ALERT_ID INTEGER DEFAULT nextval('alert_seq'), RULE VARCHAR, ACCOUNT_ID VARCHAR,
        TXN_IDS VARCHAR[], SEVERITY VARCHAR, EVIDENCE JSON, STATUS VARCHAR DEFAULT 'OPEN',
        ALERT_KEY VARCHAR, CREATED_AT TIMESTAMP DEFAULT current_timestamp);
    """)
    return db


def run_sql_file(db, name):
    text = re.sub(r"--[^\n]*", "", (ROOT / "sql" / name).read_text())
    text = text.replace("OBJECT_CONSTRUCT(", "json_object(").replace("TRANSIENT TABLE", "TABLE")
    text = text.replace(" COPY GRANTS", "")
    text = text.replace("ARRAY_SORT(", "list_sort(").replace("VARIANT)", "JSON)")
    text = re.sub(r"\bARRAY\b(?=\s*,)", "VARCHAR[]", text)
    text = re.sub(r"(\w+\.)?EVIDENCE:(\w+)::STRING", r"json_extract_string(\1EVIDENCE, '$.\2')", text)
    text = re.sub(r"ARRAY_PREPEND\((\w+), (\w+)\)", r"list_prepend(\2, \1)", text)  # arg order differs
    for stmt in filter(str.strip, text.split(";")):
        if not any(s in stmt for s in SNOWFLAKE_ONLY):
            db.execute(stmt)


def run_detection(db):
    """Rules + cycles, exactly as on Snowflake: only new alert keys are inserted."""
    run_sql_file(db, "20_detection_rules.sql")
    rows = db.execute("SELECT TXN_ID, FROM_ACCOUNT_ID, TO_ACCOUNT_ID, AMOUNT, TXN_TS FROM TRANSACTIONS "
                      "WHERE FROM_ACCOUNT_ID IS NOT NULL AND TO_ACCOUNT_ID IS NOT NULL").fetchall()
    existing = {r[0] for r in db.execute("SELECT ALERT_KEY FROM ALERTS").fetchall()}
    for a in map(to_alert, find_cycles(rows)):
        key = alert_key(a["RULE"], a["TXN_IDS"])
        if key not in existing:
            db.execute("INSERT INTO ALERTS (RULE, ACCOUNT_ID, TXN_IDS, SEVERITY, EVIDENCE, ALERT_KEY) "
                       "VALUES (?,?,?,?,?,?)", [a["RULE"], a["ACCOUNT_ID"], a["TXN_IDS"], a["SEVERITY"],
                                                str(a["EVIDENCE"]).replace("'", '"'), key])


def detect_and_evaluate(db):
    """Everything after the data is in contract tables: rules, cycles, metrics."""
    run_detection(db)
    db.execute("CREATE VIEW ALERT_TXNS AS SELECT ALERT_ID, RULE, unnest(TXN_IDS) AS TXN_ID FROM ALERTS")
    db.execute("CREATE VIEW RULE_TYPOLOGY AS SELECT * FROM (VALUES ('STRUCTURING','STRUCTURING'),"
               "('VELOCITY','VELOCITY'),('GEO_RISK','GEO_RISK'),('ROUND_TRIP_CYCLE','ROUND_TRIP'),"
               "('PASS_THROUGH','LAYERING')) v(RULE, TYPOLOGY)")
    run_sql_file(db, "30_evaluate.sql")
    metrics = db.execute("SELECT * FROM RULE_METRICS").fetchall()
    print(" | ".join(d[0] for d in db.description))
    for r in metrics:
        print(" | ".join(str(v) for v in r))
    return metrics


def check_rerun_stable(db):
    """Escalate an alert, shuffle row order, rerun: ids, keys and statuses must not change,
    and no SQL rule may alert the same account twice."""
    snap = "SELECT ALERT_ID, ALERT_KEY, STATUS FROM ALERTS ORDER BY ALERT_ID"
    db.execute("UPDATE ALERTS SET STATUS = 'ESCALATED' WHERE ALERT_ID = (SELECT MIN(ALERT_ID) FROM ALERTS)")
    before = db.execute(snap).fetchall()
    for _ in range(3):  # several shuffles: a tie only shows when the scan order happens to flip it
        db.execute("CREATE OR REPLACE TABLE T2 AS SELECT * FROM TRANSACTIONS ORDER BY random()")
        db.execute("DROP TABLE TRANSACTIONS")
        db.execute("ALTER TABLE T2 RENAME TO TRANSACTIONS")
        run_detection(db)
    assert db.execute(snap).fetchall() == before, "rerun changed alert ids, keys or statuses"
    dupes = db.execute("""SELECT RULE, ACCOUNT_ID FROM ALERTS WHERE RULE <> 'ROUND_TRIP_CYCLE'
                          GROUP BY 1, 2 HAVING COUNT(*) > 1""").fetchall()
    assert not dupes, f"same pattern alerted twice: {dupes}"
    db.execute("UPDATE ALERTS SET STATUS = 'OPEN'")

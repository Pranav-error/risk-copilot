"""Full pipeline on the real generated data in data/, no Snowflake needed:
raw CSVs -> 02_canonical -> 05_reference -> rules -> precision/recall.

    pip install duckdb && python3 tests/run_on_data.py
"""
from duck import ROOT, connect, detect_and_evaluate, run_sql_file

db = connect()
data = ROOT / "data"
for table, f in [("RAW_CUSTOMERS", "customers"), ("RAW_ACCOUNTS", "accounts"),
                 ("RAW_TRANSACTIONS", "transactions"), ("RAW_LABELS", "ground_truth_labels"),
                 ("RAW_HIGH_RISK_COUNTRIES", "reference_high_risk_countries")]:
    db.execute(f"CREATE TABLE {table} AS SELECT * FROM read_csv_auto('{data / f}.csv')")

run_sql_file(db, "02_canonical.sql")
run_sql_file(db, "05_reference.sql")
print(db.execute("SELECT CHANNEL, COUNT(*) FROM TRANSACTIONS GROUP BY 1 ORDER BY 2 DESC").fetchall())
print(db.execute("SELECT COUNT(*) FROM TRANSACTIONS").fetchone()[0], "txns,",
      db.execute("SELECT COUNT(*) FROM TXN_LABELS").fetchone()[0], "labelled")

detect_and_evaluate(db)
print("\nmissed labelled txns by typology:")
for r in db.execute("""SELECT l.TYPOLOGY, COUNT(*) FROM TXN_LABELS l
                       WHERE l.TXN_ID NOT IN (SELECT TXN_ID FROM ALERT_TXNS) GROUP BY 1""").fetchall():
    print(" ", r)

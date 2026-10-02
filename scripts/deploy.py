"""Deploy the whole pipeline to Snowflake, in order.

    pip install "snowflake-connector-python[secure-local-storage]"
    python3 scripts/deploy.py                 # uses default connection in ~/.snowflake/connections.toml
    python3 scripts/deploy.py -c <connection> --only 20_detection_rules.sql
"""
import argparse
import tomllib
from pathlib import Path

import snowflake.connector

ROOT = Path(__file__).resolve().parent.parent
DB, SCHEMA = "RISK_COPILOT", "AML"

# file uploads that must happen before a given script runs
UPLOADS = {
    "01_load_raw.sql": [("data/*.csv", "RAW_DATA", True)],
    "10_alerts_and_cycle_proc.sql": [("detection/cycles.py", "CODE_STAGE", False)],
    "40_cortex_search.sql": [("corpus/*.pdf", "REG_DOCS", False)],
}
ORDER = ["01_load_raw.sql", "02_canonical.sql", "05_reference.sql", "10_alerts_and_cycle_proc.sql",
         "20_detection_rules.sql", "30_evaluate.sql", "50_findings.sql", "40_cortex_search.sql",
         "60_semantic_view.sql", "70_agent.sql"]
STAGES = {"RAW_DATA": "", "CODE_STAGE": "",
          "REG_DOCS": "DIRECTORY = (ENABLE = TRUE) ENCRYPTION = (TYPE = 'SNOWFLAKE_SSE')"}


def run(cur, sql):
    cur.execute(sql)
    return cur.fetchall() if cur.description else []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--connection")
    ap.add_argument("--only", nargs="*", help="script names to run (default: all, in order)")
    args = ap.parse_args()

    name = args.connection or tomllib.loads(
        (Path.home() / ".snowflake/connections.toml").read_text())["default_connection_name"]
    conn = snowflake.connector.connect(connection_name=name, login_timeout=240)
    cur = conn.cursor()
    for s in (f"CREATE DATABASE IF NOT EXISTS {DB}", f"CREATE SCHEMA IF NOT EXISTS {DB}.{SCHEMA}",
              f"USE SCHEMA {DB}.{SCHEMA}"):
        run(cur, s)
    for stage, opts in STAGES.items():
        run(cur, f"CREATE STAGE IF NOT EXISTS {stage} {opts}")

    for name in args.only or ORDER:
        for pattern, stage, compress in UPLOADS.get(name, []):
            for f in sorted(ROOT.glob(pattern)):
                run(cur, f"PUT 'file://{f}' @{stage} AUTO_COMPRESS={str(compress).upper()} OVERWRITE=TRUE")
                print(f"  uploaded {f.relative_to(ROOT)} -> @{stage}")
            if stage == "REG_DOCS":
                run(cur, "ALTER STAGE REG_DOCS REFRESH")
        print(f"== {name}")
        for c in conn.execute_string((ROOT / "sql" / name).read_text(), remove_comments=True):
            if c.description:
                rows = c.fetchall()
                cols = [d[0] for d in c.description]
                if rows and len(rows) <= 20 and not c.query.lstrip().upper().startswith(("CREATE", "INSERT", "DELETE", "UPDATE")):
                    print("   " + " | ".join(cols))
                    for r in rows:
                        print("   " + " | ".join(str(v)[:80] for v in r))
    conn.close()


if __name__ == "__main__":
    main()

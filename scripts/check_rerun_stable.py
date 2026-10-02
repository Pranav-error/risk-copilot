"""Live check on Snowflake: rerunning detection must not add, renumber or reset any alert.

    python3 scripts/check_rerun_stable.py [-c CONNECTION]

Escalates one alert, reruns every rule and the cycle proc, then compares. Restores the
alert to OPEN afterwards. Exits non-zero on any difference.
"""
import argparse
import subprocess
import sys
import tomllib
from pathlib import Path

import snowflake.connector

ap = argparse.ArgumentParser()
ap.add_argument("-c", "--connection")
args = ap.parse_args()
name = args.connection or tomllib.loads(
    (Path.home() / ".snowflake/connections.toml").read_text())["default_connection_name"]
cur = snowflake.connector.connect(connection_name=name, database="RISK_COPILOT", schema="AML",
                                  login_timeout=240).cursor()

snap = "SELECT ALERT_ID, ALERT_KEY, STATUS FROM ALERTS ORDER BY ALERT_ID"
probe = cur.execute("SELECT MIN(ALERT_ID) FROM ALERTS WHERE STATUS = 'OPEN'").fetchone()[0]
cur.execute("UPDATE ALERTS SET STATUS = 'ESCALATED' WHERE ALERT_ID = %s", (probe,))
before = cur.execute(snap).fetchall()
deploy = [sys.executable, str(Path(__file__).with_name("deploy.py")), "--only", "20_detection_rules.sql"]
subprocess.run(deploy + (["-c", name] if args.connection else []), capture_output=True, check=True)
cur.execute("CALL DETECT_ROUND_TRIPS()")
after = cur.execute(snap).fetchall()
cur.execute("UPDATE ALERTS SET STATUS = 'OPEN' WHERE ALERT_ID = %s", (probe,))

print(f"alerts {len(before)} -> {len(after)}, identical: {before == after}")
sys.exit(0 if before == after else 1)

"""Live governance check on Snowflake: each role can do exactly what it should.

    python3 scripts/check_governance.py [-c CONNECTION]

Runs with USE SECONDARY ROLES NONE, otherwise a user's other roles leak privileges into
the session and every check passes for the wrong reason. Exits non-zero on any failure.
"""
import argparse
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
                                  login_timeout=60).cursor()
cur.execute("USE SECONDARY ROLES NONE")

def allowed(sql):
    try:
        cur.execute(sql)
        return True, cur.fetchone() if cur.description else None
    except snowflake.connector.errors.ProgrammingError:
        return False, None

failures = 0
def expect(role, what, sql, should_work, check=None):
    global failures
    ok, row = allowed(sql)
    good = ok == should_work and (check is None or not ok or check(row))
    failures += not good
    print(f"{'PASS' if good else 'FAIL'}  {role:<18} {what:<46} {'allowed' if ok else 'denied'}")

probe = "SELECT MIN(ALERT_ID) FROM ALERTS"
for role in ("AML_ANALYST", "COMPLIANCE_OFFICER", "AUDITOR"):
    cur.execute(f"USE ROLE {role}")
    works_cases = role != "AUDITOR"
    expect(role, "read alerts", "SELECT COUNT(*) FROM ALERT_QUEUE", True)
    expect(role, "customer names " + ("visible" if works_cases else "masked"),
           "SELECT NAME FROM CUSTOMER_PROFILE LIMIT 1", True,
           check=lambda r, w=works_cases: (r[0] != "*** masked ***") == w)
    expect(role, "names via the AI layer " + ("visible" if works_cases else "masked"),
           "SELECT * FROM SEMANTIC_VIEW(AML_SEMANTIC_VIEW DIMENSIONS customers.customer_name "
           "METRICS customers.customer_count) LIMIT 1", True,
           check=lambda r, w=works_cases: (r[0] != "*** masked ***") == w)
    expect(role, "raw CUSTOMERS table", "SELECT NAME FROM CUSTOMERS LIMIT 1", works_cases)
    expect(role, "read ground truth (TXN_LABELS)", "SELECT COUNT(*) FROM TXN_LABELS", False)
    expect(role, "ask the agent's semantic view",
           "SELECT * FROM SEMANTIC_VIEW(AML_SEMANTIC_VIEW METRICS alerts.alert_count)", True)
    expect(role, "record a decision (INSERT FINDINGS)",
           f"INSERT INTO FINDINGS (ALERT_ID, DECISION, ANALYST, REASON) "
           f"SELECT ({probe}), 'DISMISS', 'governance-check', 'probe'", works_cases)
    expect(role, "edit a decision (UPDATE FINDINGS)",
           "UPDATE FINDINGS SET REASON = 'tampered' WHERE ANALYST = 'governance-check'", False)
    expect(role, "delete a decision (DELETE FINDINGS)",
           "DELETE FROM FINDINGS WHERE ANALYST = 'governance-check'", False)
    expect(role, "approve a SAR (UPDATE SAR_REPORTS)",
           "UPDATE SAR_REPORTS SET STATUS = STATUS WHERE 1 = 0", role == "COMPLIANCE_OFFICER")

cur.execute("USE ROLE ACCOUNTADMIN")
cur.execute("DELETE FROM FINDINGS WHERE ANALYST = 'governance-check'")
print(f"\n{failures} failure(s)")
sys.exit(1 if failures else 0)

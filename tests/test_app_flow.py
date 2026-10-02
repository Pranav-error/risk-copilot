"""Drive the full analyst flow through the Streamlit UI against live Snowflake:
escalate an alert -> draft a SAR with Cortex AI -> citation check -> officer approval.
Cleans up its own rows afterwards.

    APP_ROLE=COMPLIANCE_OFFICER python tests/test_app_flow.py [ALERT_ID]
"""
import json
import os
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "streamlit" / "streamlit_app.py")
ALERT = int(sys.argv[1]) if len(sys.argv) > 1 else 37
NAME = "AppTest Analyst"

at = AppTest.from_file(APP, default_timeout=300).run()
session = at.session_state  # noqa: F841 (keeps the app's state between runs)
inv = next(s for s in at.selectbox if s.label == "Alert")
inv.set_value(ALERT).run()
at.text_input[0].input(NAME)
at.radio[0].set_value("ESCALATE")
at.text_area[0].input("UI test: four sub-threshold cash deposits in one day, high-risk customer.")
next(b for b in at.button if b.label == "Record decision").click().run()
assert not at.exception, [e.value for e in at.exception]
print("escalated via form:", any("ESCALATED" in str(m.value) or "Already ESCALATED" in str(m.value)
                                 for m in at.success) or "see DB")

next(b for b in at.button if b.label == "Draft SAR with Cortex AI").click().run()
assert not at.exception, [e.value for e in at.exception]
sar_text = next((t.value for t in at.text), "")
print("SAR draft words:", len(sar_text.split()))
print("SAR head:", " ".join(sar_text.split())[:400])
approve = [b for b in at.button if b.label == "Approve for filing"]
print("approve button enabled:", bool(approve) and not approve[0].disabled)
if approve and not approve[0].disabled:
    approve[0].click().run()
assert not at.exception, [e.value for e in at.exception]

# verify in the database, then clean up
from snowflake.snowpark import Session  # noqa: E402

s = Session.builder.config("connection_name", os.environ.get("SNOWFLAKE_CONNECTION", "XW10571_KEY")).create()
db = "RISK_COPILOT.AML"
f = s.sql(f"SELECT DECISION, ANALYST, DECIDED_BY_ROLE FROM {db}.FINDINGS WHERE ANALYST = '{NAME}'").collect()
sar = s.sql(f"""SELECT r.SAR_ID, r.STATUS, c.TXNS_CITED, c.VERIFIED, c.UNVERIFIED, r.NARRATIVE
               FROM {db}.SAR_REPORTS r LEFT JOIN {db}.SAR_CITATION_CHECK c USING (SAR_ID)
               WHERE r.ALERT_ID = {ALERT} ORDER BY r.SAR_ID DESC LIMIT 1""").collect()
st = s.sql(f"SELECT STATUS FROM {db}.ALERTS WHERE ALERT_ID = {ALERT}").collect()[0][0]
print("FINDINGS:", [tuple(r) for r in f])
print("alert status:", st)
if sar:
    r = sar[0]
    import re
    print("SAR:", r["SAR_ID"], r["STATUS"], f"citations {r['VERIFIED']}/{r['TXNS_CITED']}", "unverified", r["UNVERIFIED"])
    print("no legal conclusion:", not re.search(r"\bviolat|\bcommitted\b|\bguilty\b", r["NARRATIVE"], re.I))
    print("no invented timezone:", "UTC" not in r["NARRATIVE"])
    print("severity reason quoted:", "7.2" in r["NARRATIVE"])
s.sql(f"DELETE FROM {db}.SAR_REPORTS WHERE ALERT_ID = {ALERT} AND DRAFTED_BY LIKE 'Streamlit app%'").collect()
s.sql(f"DELETE FROM {db}.FINDINGS WHERE ANALYST = '{NAME}'").collect()
s.sql(f"UPDATE {db}.ALERTS SET STATUS = 'OPEN' WHERE ALERT_ID = {ALERT}").collect()
print("cleaned up")

"""Headless run of the Streamlit app against live Snowflake, as each role.

    APP_ROLE=AUDITOR python tests/test_app.py      (needs streamlit + snowflake-snowpark-python)
"""
import os
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

role = os.environ.get("APP_ROLE", "COMPLIANCE_OFFICER")
at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "streamlit" / "streamlit_app.py"),
                       default_timeout=180).run()
problems = [e.value for e in at.exception]
print(f"role {role}: {len(at.exception)} exception(s)")
for p in problems:
    print("  ", str(p)[:400])
print("  caption:", at.caption[0].value if at.caption else None)
print("  metrics:", [(m.label, m.value) for m in at.metric])
print("  dataframes rendered:", len(at.dataframe))
print("  decision form present:", any(b.label == "Record decision" for b in at.button))
kyc = [d.value for d in at.dataframe]
masked = any("*** masked ***" in df.to_string() for df in kyc)
print("  PII masked in KYC table:", masked)
sys.exit(1 if problems else 0)

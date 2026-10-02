"""Ask the copilot one question through the Streamlit chat tab (live Snowflake)."""
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "streamlit" / "streamlit_app.py"),
                       default_timeout=300).run()
at.chat_input[0].set_value(sys.argv[1] if len(sys.argv) > 1 else
                           "Which 3 high-risk customers deposited the most cash, and how much?").run()
assert not at.exception, [e.value for e in at.exception]
msgs = at.chat_message
print("messages:", len(msgs))
print("answer:", " ".join(msgs[-1].markdown[0].value.split())[:500])
print("tables in answer:", len(msgs[-1].dataframe))
if msgs[-1].dataframe:
    print(msgs[-1].dataframe[0].value.head(5).to_string())

"""AML Risk Copilot — command centre (Streamlit in Snowflake).

Deployed twice by sql/90_streamlit.sql: owned by COMPLIANCE_OFFICER (works cases) and by
AUDITOR (read-only, PII masked). A Streamlit app runs with its owner's role, so Snowflake —
not this code — enforces what each one can see and do. The UI only hides buttons that
would fail anyway.
"""
import json
import re

import pandas as pd
import streamlit as st

try:
    from snowflake.snowpark.context import get_active_session
    session = get_active_session()
except Exception:  # running locally: python -m streamlit run streamlit/streamlit_app.py
    from snowflake.snowpark import Session
    import os
    session = Session.builder.config("connection_name", os.environ.get("SNOWFLAKE_CONNECTION", "XW10571_KEY")).create()
    if os.environ.get("APP_ROLE"):
        session.sql("USE SECONDARY ROLES NONE").collect()
        session.sql(f"USE ROLE {os.environ['APP_ROLE']}").collect()

DB = "RISK_COPILOT.AML"
MODEL = "claude-sonnet-4-5"
SEV_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}

st.set_page_config(page_title="AML Risk Copilot", page_icon="🛡️", layout="wide")


def q(sql, params=None):
    return session.sql(sql, params=params).to_pandas()


def md(text):
    """Make model / document text safe for st.markdown: "$75,815.11 and $54,553.74" would
    otherwise render as a LaTeX formula, and markdown-source ** leaks from the policy PDF."""
    return (text or "").replace("**", "").replace("$", "\\$")


ROLE = q("SELECT CURRENT_ROLE() AS R")["R"][0]
CAN_DECIDE = ROLE in ("AML_ANALYST", "COMPLIANCE_OFFICER", "ACCOUNTADMIN")
CAN_APPROVE = ROLE in ("COMPLIANCE_OFFICER", "ACCOUNTADMIN")

st.title("🛡️ AML Risk Copilot")
st.caption(f"Signal → evidence → documented finding → SAR · role **{ROLE}**"
           + ("" if CAN_DECIDE else " · read-only, customer PII masked"))

tab_queue, tab_inv, tab_ask, tab_sar, tab_audit = st.tabs(
    ["Alert queue", "Investigate", "Ask the copilot", "SARs", "Audit & metrics"])


# ---------------------------------------------------------------- queue
@st.cache_data(ttl=30)
def load_queue():
    df = q(f"""SELECT ALERT_ID, RULE, ACCOUNT_ID, EFFECTIVE_SEVERITY AS SEVERITY, STATUS,
                      EVIDENCE:policy::STRING AS POLICY, SEVERITY_REASON, ARRAY_SIZE(TXN_IDS) AS TXNS
               FROM {DB}.ALERT_QUEUE""")
    df["_o"] = df["SEVERITY"].map(SEV_ORDER)
    return df.sort_values(["_o", "ALERT_ID"]).drop(columns="_o")


with tab_queue:
    df = load_queue()
    sars = q(f"SELECT COUNT(*) AS N FROM {DB}.SAR_REVIEW")["N"][0]
    c = st.columns(4)
    c[0].metric("Open alerts", int((df.STATUS == "OPEN").sum()))
    c[1].metric("Critical", int((df.SEVERITY == "CRITICAL").sum()))
    c[2].metric("Escalated", int((df.STATUS == "ESCALATED").sum()))
    c[3].metric("SARs", int(sars))
    f = st.columns(3)
    rules = f[0].multiselect("Rule", sorted(df.RULE.unique()))
    sevs = f[1].multiselect("Severity", list(SEV_ORDER))
    stat = f[2].multiselect("Status", ["OPEN", "ESCALATED", "DISMISSED"], default=["OPEN"])
    keep = pd.Series(True, index=df.index)
    for col, chosen in (("RULE", rules), ("SEVERITY", sevs), ("STATUS", stat)):
        if chosen:
            keep &= df[col].isin(chosen)
    view = df[keep]
    st.dataframe(view, hide_index=True, use_container_width=True)
    st.bar_chart(df.groupby(["RULE"]).size().rename("alerts"))


# ---------------------------------------------------------------- investigate
def policy_text(clause):
    """The clause text from Cortex Search, so the analyst reads the rule, not our summary."""
    if not clause:
        return None
    # filtered to the bank's own policy: with FATF and FinCEN in the corpus, an unfiltered
    # "policy section 6.2" query returns regulator text that merely mentions the same words
    req = json.dumps({"query": f"policy section {clause} rule", "columns": ["SOURCE", "CHUNK"], "limit": 3,
                      "filter": {"@eq": {"SOURCE": "internal_aml_policy.pdf"}}})
    hits = json.loads(q("SELECT SNOWFLAKE.CORTEX.SEARCH_PREVIEW(?, ?) AS R",
                        [f"{DB}.AML_POLICY_SEARCH", req])["R"][0])["results"]
    for h in hits:
        m = re.search(rf"{re.escape(clause)} (.+?)(?=\s\d+\.\d+ |$)", " ".join(h["CHUNK"].split()))
        if m:
            return f"§{clause} {m.group(1).replace('**', '')}"
    return None


with tab_inv:
    df = load_queue()
    alert_id = st.selectbox("Alert", df.ALERT_ID.tolist(),
                            format_func=lambda a: "{} · {} · {} · {}".format(
                                a, *df.set_index("ALERT_ID").loc[a, ["RULE", "SEVERITY", "STATUS"]]))
    a = q(f"SELECT * FROM {DB}.ALERT_QUEUE WHERE ALERT_ID = ?", [alert_id]).iloc[0]
    ev = json.loads(a.EVIDENCE)
    st.subheader(f"Alert {alert_id} — {a.RULE} on {a.ACCOUNT_ID}")
    c = st.columns([1, 3])
    c[0].metric("Severity", a.EFFECTIVE_SEVERITY)
    c[1].info(a.SEVERITY_REASON)
    clause_text = policy_text(ev.get("policy"))
    if clause_text:
        st.markdown(f"**Policy:** {md(clause_text)}  \n*source: internal_aml_policy.pdf via Cortex Search*")
    st.markdown("**Rule evidence** (the numbers that fired it)")
    st.dataframe(pd.DataFrame([(k.replace("_", " "), str(v)) for k, v in ev.items() if k != "policy"],
                              columns=["measure", "value"]), hide_index=True)

    st.markdown("**Transactions that fired the rule**")
    st.dataframe(q(f"""SELECT t.TXN_ID, t.TXN_TS, t.FROM_ACCOUNT_ID, t.TO_ACCOUNT_ID, t.AMOUNT,
                              t.CHANNEL, t.COUNTERPARTY_COUNTRY, t.DESCRIPTION
                       FROM {DB}.TRANSACTIONS t JOIN {DB}.ALERT_TXNS x ON x.TXN_ID = t.TXN_ID
                       WHERE x.ALERT_ID = ? ORDER BY t.TXN_TS""", [alert_id]),
                 hide_index=True, use_container_width=True)
    left, right = st.columns(2)
    with left:
        st.markdown("**Customer (KYC)**")
        st.dataframe(q(f"""SELECT p.* FROM {DB}.CUSTOMER_PROFILE p
                           JOIN {DB}.ACCOUNTS a ON a.CUSTOMER_ID = p.CUSTOMER_ID
                           WHERE a.ACCOUNT_ID = ?""", [a.ACCOUNT_ID]).T.astype(str).set_axis(["value"], axis=1),
                     use_container_width=True)
    with right:
        st.markdown("**Linked alerts** (this account or its counterparties)")
        st.dataframe(q(f"""SELECT ALERT_ID, RULE, ACCOUNT_ID, EFFECTIVE_SEVERITY, STATUS FROM {DB}.ALERT_QUEUE
                           WHERE ALERT_ID <> ? AND ACCOUNT_ID IN (
                             SELECT FROM_ACCOUNT_ID FROM {DB}.TRANSACTIONS t JOIN {DB}.ALERT_TXNS x
                               ON x.TXN_ID = t.TXN_ID WHERE x.ALERT_ID = ?
                             UNION SELECT TO_ACCOUNT_ID FROM {DB}.TRANSACTIONS t JOIN {DB}.ALERT_TXNS x
                               ON x.TXN_ID = t.TXN_ID WHERE x.ALERT_ID = ?)""",
                       [alert_id, alert_id, alert_id]), hide_index=True, use_container_width=True)

    st.markdown("**Decision**")
    if not CAN_DECIDE:
        st.caption("Read-only role: decisions are recorded by analysts.")
    elif a.STATUS != "OPEN":
        st.success(f"Already {a.STATUS}. Decisions are append-only; see Audit.")
    else:
        with st.form("decide"):
            analyst = st.text_input("Analyst name")
            decision = st.radio("Decision", ["ESCALATE", "DISMISS"], horizontal=True)
            reason = st.text_area("Reason (required: what in the evidence supports this)")
            if st.form_submit_button("Record decision"):
                if not analyst.strip() or not reason.strip():
                    st.error("Name and reason are required.")
                else:
                    session.sql(f"""INSERT INTO {DB}.FINDINGS (ALERT_ID, DECISION, ANALYST, REASON,
                                       EVIDENCE_TXN_IDS, POLICY_REFS)
                                    SELECT ?, ?, ?, ?, TXN_IDS, ARRAY_CONSTRUCT('§' || EVIDENCE:policy::STRING)
                                    FROM {DB}.ALERTS WHERE ALERT_ID = ?""",
                                params=[int(alert_id), decision, analyst.strip(), reason.strip(),
                                        int(alert_id)]).collect()
                    session.sql(f"UPDATE {DB}.ALERTS SET STATUS = ? WHERE ALERT_ID = ?",
                                params=["ESCALATED" if decision == "ESCALATE" else "DISMISSED",
                                        int(alert_id)]).collect()
                    load_queue.clear()
                    st.success(f"Recorded: {decision} by {analyst}.")
                    st.rerun()


# ---------------------------------------------------------------- ask
def ask_agent(history):
    body = {"messages": history}
    raw = q("SELECT SNOWFLAKE.CORTEX.DATA_AGENT_RUN(?, ?) AS R", [f"{DB}.AML_COPILOT", json.dumps(body)])["R"][0]
    resp = json.loads(raw)
    text, tables, sqls = [], [], []
    for c in resp.get("content", []):
        t = c.get("type")
        if t == "text":
            text.append(c["text"])
        elif t == "table":
            rs = c["table"]["result_set"]
            cols = [r["name"] for r in rs["resultSetMetaData"]["rowType"]]
            tables.append(pd.DataFrame(rs["data"], columns=cols))
        elif t == "tool_result":
            for item in c["tool_result"].get("content", []):
                if (item.get("json") or {}).get("sql"):
                    sqls.append(item["json"]["sql"])
    return "\n".join(text).strip(), tables, sqls


with tab_ask:
    st.caption("Cortex Agent over the semantic view (data) and Cortex Search (policy, FinCEN). "
               "It explains; it never decides.")
    st.session_state.setdefault("chat", [])
    for m in st.session_state.chat:
        with st.chat_message(m["role"]):
            st.markdown(md(m["text"]))
            for t in m.get("tables", []):
                st.dataframe(t, hide_index=True)
            for s in m.get("sqls", []):
                with st.expander("SQL generated by Cortex Analyst"):
                    st.code(s, language="sql")
    prompt = st.chat_input("e.g. Which high-risk customers deposited the most cash?")
    if prompt:
        st.session_state.chat.append({"role": "user", "text": prompt})
        history = [{"role": m["role"], "content": [{"type": "text", "text": m["text"]}]}
                   for m in st.session_state.chat]
        with st.spinner("Asking the copilot…"):
            text, tables, sqls = ask_agent(history)
        st.session_state.chat.append({"role": "assistant", "text": text or "_(no answer)_",
                                      "tables": tables, "sqls": sqls})
        st.rerun()


# ---------------------------------------------------------------- SARs
SAR_RULES = """Write a FinCEN SAR narrative from the evidence below.
Sections: Introduction; Who; What; When; Where; Why it is suspicious; Analyst review.
Rules:
- No legal conclusions. Never say the subject violated, committed or is guilty of anything;
  say what the activity appears consistent with.
- Put a citation after each claim: [TXN <ids>] or [Policy §<clause>]. Use only transaction IDs
  that appear in the evidence. A claim you cannot cite does not go in.
- State the severity and its reason exactly as given in SEVERITY_REASON.
- Cite a policy clause as met only if its threshold is met, with the numbers.
- Timestamps have no timezone; do not add one.
- Plain text, no markdown headings symbols, under 450 words."""


def draft_sar(alert_id):
    a = q(f"SELECT * FROM {DB}.ALERT_QUEUE WHERE ALERT_ID = ?", [alert_id]).iloc[0]
    txns = q(f"""SELECT t.TXN_ID, t.TXN_TS, t.FROM_ACCOUNT_ID, t.TO_ACCOUNT_ID, t.AMOUNT, t.CHANNEL,
                        t.ACCESS_POINT, t.COUNTERPARTY_COUNTRY
                 FROM {DB}.TRANSACTIONS t JOIN {DB}.ALERT_TXNS x ON x.TXN_ID = t.TXN_ID
                 WHERE x.ALERT_ID = ? ORDER BY t.TXN_TS""", [alert_id])
    cust = q(f"""SELECT p.* FROM {DB}.CUSTOMER_PROFILE p JOIN {DB}.ACCOUNTS c ON c.CUSTOMER_ID = p.CUSTOMER_ID
                 WHERE c.ACCOUNT_ID = ?""", [a.ACCOUNT_ID])
    fin = q(f"""SELECT ANALYST, REASON, DECIDED_AT FROM {DB}.FINDINGS
                WHERE ALERT_ID = ? AND DECISION = 'ESCALATE' ORDER BY DECIDED_AT DESC LIMIT 1""", [alert_id])
    evidence = {
        "alert": {"id": int(alert_id), "rule": a.RULE, "account": a.ACCOUNT_ID,
                  "effective_severity": a.EFFECTIVE_SEVERITY, "SEVERITY_REASON": a.SEVERITY_REASON,
                  "rule_evidence": json.loads(a.EVIDENCE),
                  "policy_clause_text": policy_text(json.loads(a.EVIDENCE).get("policy"))},
        "transactions": json.loads(txns.to_json(orient="records", date_format="iso")),
        "customer": json.loads(cust.to_json(orient="records", date_format="iso")),
        "analyst_escalation": json.loads(fin.to_json(orient="records", date_format="iso")),
    }
    narrative = q("SELECT AI_COMPLETE(?, ?) AS R",
                  [MODEL, SAR_RULES + "\n\nEVIDENCE:\n" + json.dumps(evidence, default=str)])["R"][0]
    narrative = json.loads(narrative) if narrative.startswith('"') else narrative
    narrative = re.sub(r"[*#]{1,3}", "", narrative).strip()  # rendered as plain text
    cited = sorted(set(re.findall(r"TXN_[0-9A-F]{12}", narrative)))
    session.sql(f"""INSERT INTO {DB}.SAR_REPORTS (ALERT_ID, STATUS, NARRATIVE, CITED_TXN_IDS, CITED_SOURCES, DRAFTED_BY)
                    SELECT ?, 'DRAFT', ?, PARSE_JSON(?), PARSE_JSON(?), ?""",
                params=[int(alert_id), narrative, json.dumps(cited),
                        json.dumps(sorted(set(re.findall(r"Policy §[\d.]+", narrative)))),
                        f"Streamlit app ({MODEL})"]).collect()


with tab_sar:
    pending = q(f"""SELECT a.ALERT_ID, a.RULE, a.ACCOUNT_ID FROM {DB}.ALERTS a
                    WHERE a.STATUS = 'ESCALATED'
                      AND a.ALERT_ID NOT IN (SELECT ALERT_ID FROM {DB}.SAR_REVIEW)""")
    if CAN_DECIDE and len(pending):
        st.markdown("**Escalated, awaiting a SAR draft**")
        pick = st.selectbox("Alert", pending.ALERT_ID.tolist(), key="sarpick")
        if st.button("Draft SAR with Cortex AI"):
            with st.spinner("Drafting from the evidence…"):
                draft_sar(pick)
            st.rerun()
    elif CAN_DECIDE:
        st.caption("No escalated alerts waiting. A SAR can only be drafted after a human escalates.")

    sars = q(f"""SELECT s.SAR_ID, s.ALERT_ID, s.STATUS, s.NARRATIVE, s.DRAFTED_BY, s.CREATED_AT,
                        c.TXNS_CITED, c.VERIFIED, c.UNVERIFIED
                 FROM {DB}.SAR_REVIEW s LEFT JOIN {DB}.SAR_CITATION_CHECK c ON c.SAR_ID = s.SAR_ID
                 ORDER BY s.SAR_ID DESC""")
    for _, s in sars.iterrows():
        unverified = [x for x in json.loads(s.UNVERIFIED or "[]") if x]
        ok = not unverified
        with st.expander(f"SAR {s.SAR_ID} · alert {s.ALERT_ID} · {s.STATUS} · citations "
                         f"{int(s.VERIFIED or 0)}/{int(s.TXNS_CITED or 0)} {'✅' if ok else '❌'}",
                         expanded=s.STATUS == "DRAFT"):
            st.text(s.NARRATIVE)
            st.caption(f"Drafted by {s.DRAFTED_BY} · {s.CREATED_AT}")
            if unverified:
                st.error(f"Unverified transaction IDs: {', '.join(unverified)}. Cannot be approved.")
            if CAN_APPROVE and s.STATUS == "DRAFT":
                if st.button("Approve for filing", key=f"approve{s.SAR_ID}", disabled=not ok):
                    session.sql(f"UPDATE {DB}.SAR_REPORTS SET STATUS = 'APPROVED' WHERE SAR_ID = ?",
                                params=[int(s.SAR_ID)]).collect()
                    st.rerun()
            elif s.STATUS == "DRAFT":
                st.caption("Approval requires the COMPLIANCE_OFFICER role.")


# ---------------------------------------------------------------- audit
with tab_audit:
    st.markdown("**Decision log** — append-only: no role can update or delete a finding")
    st.dataframe(q(f"""SELECT FINDING_ID, ALERT_ID, DECISION, ANALYST, REASON, POLICY_REFS,
                              DECIDED_BY_ROLE, DECIDED_AT FROM {DB}.FINDINGS ORDER BY FINDING_ID DESC"""),
                 hide_index=True, use_container_width=True)
    st.markdown("**Rule performance** against seeded ground truth (the rules never read the labels)")
    st.dataframe(q(f"""SELECT RULE, ALERTS, PRECISION, LABELLED_CASES, CAUGHT_CASES, CASE_RECALL
                       FROM {DB}.RULE_METRICS ORDER BY RULE"""), hide_index=True, use_container_width=True)

"""AML Risk Copilot — command centre (Streamlit in Snowflake).

Deployed twice by sql/90_streamlit.sql: owned by COMPLIANCE_OFFICER (works cases) and by
AUDITOR (read-only, PII masked). A Streamlit app runs with its owner's role, so Snowflake —
not this code — enforces what each one can see and do. The UI only hides buttons that
would fail anyway.
"""
import html
import json
import re

import altair as alt
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
SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
SEV_ORDER = {s: i for i, s in enumerate(SEVERITIES)}
SEV_COLOR = {"CRITICAL": "#E5484D", "HIGH": "#F76B15", "MEDIUM": "#E5A000", "LOW": "#3E63DD"}
STATUS_COLOR = {"OPEN": "#8B8D98", "ESCALATED": "#E5484D", "DISMISSED": "#30A46C",
                "DRAFT": "#E5A000", "APPROVED": "#30A46C", "FILED": "#3E63DD"}
RULE_LABEL = {"STRUCTURING": "Structuring", "VELOCITY": "Velocity spike", "PASS_THROUGH": "Pass-through",
              "GEO_RISK": "High-risk geography", "ROUND_TRIP_CYCLE": "Round-trip cycle"}

st.set_page_config(page_title="AML Risk Copilot", page_icon="🛡️", layout="wide")

# Colours are translucent so the same CSS reads on Snowsight's light and dark themes.
st.markdown("""
<style>
.block-container {padding-top: 3.2rem; max-width: 1400px;}
.hero {padding: 1.1rem 1.4rem; border-radius: 14px; margin-bottom: .6rem;
       background: linear-gradient(120deg, rgba(41,181,232,.16), rgba(62,99,221,.10) 55%, rgba(229,72,77,.08));
       border: 1px solid rgba(128,128,128,.22);}
.hero h1 {font-size: 1.75rem; margin: 0; padding: 0;}
.hero .sub {opacity: .75; margin-top: .2rem; font-size: .95rem;}
.flow {display: flex; gap: .4rem; flex-wrap: wrap; margin-top: .65rem;}
.flow span {font-size: .78rem; padding: .18rem .6rem; border-radius: 999px;
            border: 1px solid rgba(128,128,128,.3); opacity: .9;}
.card {border: 1px solid rgba(128,128,128,.22); border-radius: 12px; padding: .85rem 1rem;
       background: rgba(128,128,128,.05); height: 100%;}
.kpi .label {font-size: .78rem; text-transform: uppercase; letter-spacing: .04em; opacity: .7;}
.kpi .value {font-size: 1.9rem; font-weight: 700; line-height: 1.15; margin-top: .15rem;}
.kpi .note {font-size: .78rem; opacity: .65;}
.kpi .value.sm {font-size: 1.15rem; padding-top: .45rem;}
.pill {display: inline-block; padding: .12rem .55rem; border-radius: 999px; font-size: .74rem;
       font-weight: 650; color: #fff; letter-spacing: .02em; margin-right: .3rem;}
.case h2 {margin: .1rem 0 .35rem 0; font-size: 1.45rem;}
.reason {border-left: 4px solid; padding: .55rem .85rem; border-radius: 6px;
         background: rgba(128,128,128,.07); margin: .3rem 0 .2rem 0;}
.quote {border-left: 4px solid #29B5E8; padding: .6rem .9rem; border-radius: 6px;
        background: rgba(41,181,232,.08); font-size: .95rem;}
.quote .src {font-size: .75rem; opacity: .65; margin-top: .3rem;}
.prio {border-left: 4px solid; padding: .5rem .8rem; border-radius: 8px; margin-bottom: .45rem;
       background: rgba(128,128,128,.06);}
.prio .t {font-weight: 650;} .prio .r {font-size: .82rem; opacity: .75;}
.section {font-size: 1.02rem; font-weight: 650; margin: 1rem 0 .4rem 0;}
.muted {opacity: .65; font-size: .85rem;}
</style>
""", unsafe_allow_html=True)


def q(sql, params=None):
    return session.sql(sql, params=params).to_pandas()


def md(text):
    """Make model / document text safe for st.markdown: "$75,815.11 and $54,553.74" would
    otherwise render as a LaTeX formula, and markdown-source ** leaks from the policy PDF."""
    return (text or "").replace("**", "").replace("$", "\\$")


def pill(text, color):
    return f'<span class="pill" style="background:{color}">{html.escape(str(text))}</span>'


def kpi(col, label, value, note="", color=None):
    style = f"border-top: 3px solid {color};" if color else ""
    size = " sm" if len(str(value)) > 12 else ""
    col.markdown(f'<div class="card kpi" style="{style}"><div class="label">{label}</div>'
                 f'<div class="value{size}">{value}</div><div class="note">{note or "&nbsp;"}</div></div>',
                 unsafe_allow_html=True)


def section(title, note=""):
    st.markdown(f'<div class="section">{title} <span class="muted">{note}</span></div>',
                unsafe_allow_html=True)


ROLE = q("SELECT CURRENT_ROLE() AS R")["R"][0]
CAN_DECIDE = ROLE in ("AML_ANALYST", "COMPLIANCE_OFFICER", "ACCOUNTADMIN")
CAN_APPROVE = ROLE in ("COMPLIANCE_OFFICER", "ACCOUNTADMIN")


@st.cache_data(ttl=30)
def load_queue():
    df = q(f"""SELECT ALERT_ID, RULE, ACCOUNT_ID, EFFECTIVE_SEVERITY AS SEVERITY, STATUS,
                      EVIDENCE:policy::STRING AS POLICY, SEVERITY_REASON, ARRAY_SIZE(TXN_IDS) AS TXNS
               FROM {DB}.ALERT_QUEUE""")
    df["_o"] = df["SEVERITY"].map(SEV_ORDER)
    return df.sort_values(["_o", "ALERT_ID"]).drop(columns="_o")


# ---------------------------------------------------------------- header + sidebar
st.markdown(f"""
<div class="hero">
  <h1>🛡️ AML Risk Copilot</h1>
  <div class="sub">Fraud &amp; AML monitoring that takes an analyst from a signal to a cited,
  machine-checked SAR — entirely inside Snowflake.</div>
  <div class="flow"><span>① Deterministic rules raise alerts</span><span>② Evidence + cited policy</span>
  <span>③ Human decision, append-only</span><span>④ AI-drafted SAR, citations verified</span></div>
</div>""", unsafe_allow_html=True)
st.caption(f"Signal → evidence → documented finding → SAR · role **{ROLE}**"
           + ("" if CAN_DECIDE else " · read-only, customer PII masked"))

with st.sidebar:
    st.markdown("### 🛡️ Risk Copilot")
    st.markdown(pill(ROLE, "#3E63DD" if CAN_DECIDE else "#8B8D98"), unsafe_allow_html=True)
    abilities = [("View alerts and evidence", True), ("See customer names", CAN_DECIDE),
                 ("Record decisions", CAN_DECIDE), ("Draft SARs", CAN_DECIDE),
                 ("Approve SARs", CAN_APPROVE), ("Edit or delete a decision", False)]
    st.markdown("  \n".join(f"{'✅' if ok else '🚫'} {a}" for a, ok in abilities))
    st.caption("Permissions are enforced by Snowflake roles, not by this app.")
    st.divider()
    st.markdown("**Built on Snowflake**")
    st.caption("Snowpark · Cortex Search (hybrid) · Semantic View + Cortex Analyst · "
               "Cortex Agent · AI_COMPLETE · Streamlit in Snowflake · CoCo CLI")
    st.caption("Detection is deterministic. The AI explains and drafts; a named human decides.")

tab_queue, tab_inv, tab_ask, tab_sar, tab_audit = st.tabs(
    ["📋 Alert queue", "🔎 Investigate", "💬 Ask the copilot", "📝 SARs", "📊 Audit & metrics"])


# ---------------------------------------------------------------- queue
with tab_queue:
    df = load_queue()
    sars = q(f"SELECT COUNT(*) AS N, COUNT_IF(STATUS = 'APPROVED') AS A FROM {DB}.SAR_REVIEW").iloc[0]
    c = st.columns(5)
    kpi(c[0], "Open alerts", int((df.STATUS == "OPEN").sum()), f"of {len(df)} raised")
    kpi(c[1], "Critical", int((df.SEVERITY == "CRITICAL").sum()), "needs action first", SEV_COLOR["CRITICAL"])
    kpi(c[2], "High", int((df.SEVERITY == "HIGH").sum()), "investigate today", SEV_COLOR["HIGH"])
    kpi(c[3], "Escalated", int((df.STATUS == "ESCALATED").sum()), "by a named analyst", STATUS_COLOR["ESCALATED"])
    kpi(c[4], "SARs", int(sars.N), f"{int(sars.A)} approved", STATUS_COLOR["APPROVED"])

    left, right = st.columns([3, 2])
    with left:
        section("Alerts by rule and severity")
        counts = df.groupby(["RULE", "SEVERITY"]).size().reset_index(name="ALERTS")
        counts["RULE"] = counts["RULE"].map(RULE_LABEL)
        chart = alt.Chart(counts).mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4).encode(
            y=alt.Y("RULE:N", title=None, sort="-x", axis=alt.Axis(labelLimit=220)),
            x=alt.X("sum(ALERTS):Q", title="alerts"),
            color=alt.Color("SEVERITY:N", scale=alt.Scale(domain=SEVERITIES, range=[SEV_COLOR[s] for s in SEVERITIES]),
                            legend=alt.Legend(orient="bottom", title=None)),
            order=alt.Order("SEVERITY:N"),
            tooltip=["RULE", "SEVERITY", "ALERTS"]).properties(height=230)
        st.altair_chart(chart, use_container_width=True)
    with right:
        section("Top priority", "open, most severe first")
        for _, r in df[df.STATUS == "OPEN"].head(4).iterrows():
            st.markdown(f'<div class="prio" style="border-color:{SEV_COLOR[r.SEVERITY]}">'
                        f'<div class="t">{pill(r.SEVERITY, SEV_COLOR[r.SEVERITY])}Alert {r.ALERT_ID} · '
                        f'{RULE_LABEL[r.RULE]}</div><div class="r">{html.escape(r.ACCOUNT_ID)} · policy §{r.POLICY}'
                        + ("" if r.SEVERITY_REASON.startswith("As raised") else f" · {html.escape(r.SEVERITY_REASON)}")
                        + '</div></div>', unsafe_allow_html=True)
        st.caption("Open any of these in 🔎 Investigate.")

    section("Queue")
    f = st.columns(3)
    rules = f[0].multiselect("Rule", sorted(df.RULE.unique()))
    sevs = f[1].multiselect("Severity", SEVERITIES)
    stat = f[2].multiselect("Status", ["OPEN", "ESCALATED", "DISMISSED"], default=["OPEN"])
    keep = pd.Series(True, index=df.index)
    for col, chosen in (("RULE", rules), ("SEVERITY", sevs), ("STATUS", stat)):
        if chosen:
            keep &= df[col].isin(chosen)
    view = df[keep].copy()
    view["SEVERITY"] = view["SEVERITY"].map(lambda s: {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🔵"}[s] + " " + s)
    view["RULE"] = view["RULE"].map(RULE_LABEL)
    st.dataframe(view, hide_index=True, use_container_width=True,
                 column_config={"ALERT_ID": st.column_config.NumberColumn("Alert", format="%d"),
                                "RULE": "Rule", "ACCOUNT_ID": "Account", "SEVERITY": "Severity",
                                "STATUS": "Status", "POLICY": "Policy §", "SEVERITY_REASON": "Why this severity",
                                "TXNS": st.column_config.NumberColumn("Txns", format="%d")})


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


def money_flow(txns, focus):
    """Graphviz DOT of the alert's transactions: accounts as nodes, transfers as arrows.
    Loops and fan-outs are what an analyst needs to see at a glance."""
    def node(acct, country):
        return acct if acct else f"External ({country})"
    lines = ['digraph G { rankdir=LR; bgcolor="transparent"; pad=0.2; nodesep=0.5;',
             'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11, '
             'fillcolor="#EEF2FF", color="#3E63DD", fontcolor="#11181C"];',
             'edge [fontname="Helvetica", fontsize=10, color="#8B8D98", fontcolor="#8B8D98"];']
    seen = set()
    for _, t in txns.iterrows():
        src, dst = node(t.FROM_ACCOUNT_ID, t.COUNTERPARTY_COUNTRY), node(t.TO_ACCOUNT_ID, t.COUNTERPARTY_COUNTRY)
        if not t.FROM_ACCOUNT_ID and t.CHANNEL == "CASH":
            src = "Cash"
        for n in (src, dst):
            if n not in seen:
                seen.add(n)
                extra = (', fillcolor="#FFE5E5", color="#E5484D", penwidth=2' if n == focus else
                         ', shape=ellipse, fillcolor="#F1F3F5", color="#8B8D98"' if not n.startswith("ACC_") else "")
                lines.append(f'"{n}" [label="{n}"{extra}];')
        lines.append(f'"{src}" -> "{dst}" [label="${t.AMOUNT:,.0f}\\n{pd.Timestamp(t.TXN_TS):%b %d %H:%M}"];')
    lines.append("}")
    return "\n".join(lines)


with tab_inv:
    df = load_queue()
    alert_id = st.selectbox("Alert", df.ALERT_ID.tolist(),
                            format_func=lambda a: "{} · {} · {} · {}".format(
                                a, *df.set_index("ALERT_ID").loc[a, ["RULE", "SEVERITY", "STATUS"]]))
    a = q(f"SELECT * FROM {DB}.ALERT_QUEUE WHERE ALERT_ID = ?", [alert_id]).iloc[0]
    ev = json.loads(a.EVIDENCE)
    sev_c = SEV_COLOR[a.EFFECTIVE_SEVERITY]
    st.markdown(f'<div class="case"><h2>Alert {alert_id} — {RULE_LABEL[a.RULE]} on {html.escape(a.ACCOUNT_ID)}</h2>'
                f'{pill(a.EFFECTIVE_SEVERITY, sev_c)}{pill(a.STATUS, STATUS_COLOR[a.STATUS])}'
                f'{pill("policy §" + str(ev.get("policy")), "#29B5E8")}</div>'
                f'<div class="reason" style="border-color:{sev_c}"><b>Why {a.EFFECTIVE_SEVERITY}:</b> '
                f'{html.escape(a.SEVERITY_REASON)}</div>', unsafe_allow_html=True)

    clause_text = policy_text(ev.get("policy"))
    if clause_text:
        st.markdown(f'<div class="quote">{html.escape(clause_text)}'
                    f'<div class="src">internal_aml_policy.pdf · retrieved by Cortex Search</div></div>',
                    unsafe_allow_html=True)

    section("Rule evidence", "the numbers that fired it")
    measures = [(k, v) for k, v in ev.items() if k != "policy"]
    cols = st.columns(min(len(measures), 5) or 1)
    for i, (k, v) in enumerate(measures):
        if isinstance(v, (int, float)) and any(w in k for w in ("total", "amount", "inflow", "outflow", "value", "threshold")):
            shown = f"${v:,.0f}"
        elif isinstance(v, list):
            shown = " → ".join(map(str, v))
        elif isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}", v):
            shown = f"{pd.Timestamp(v):%b %d, %H:%M}"
        else:
            shown = f"{v:,}" if isinstance(v, (int, float)) else str(v)[:40]
        kpi(cols[i % len(cols)], k.replace("_", " "), shown)

    txns = q(f"""SELECT t.TXN_ID, t.TXN_TS, t.FROM_ACCOUNT_ID, t.TO_ACCOUNT_ID, t.AMOUNT,
                        t.CHANNEL, t.COUNTERPARTY_COUNTRY, t.DESCRIPTION
                 FROM {DB}.TRANSACTIONS t JOIN {DB}.ALERT_TXNS x ON x.TXN_ID = t.TXN_ID
                 WHERE x.ALERT_ID = ? ORDER BY t.TXN_TS""", [alert_id])
    g, tl = st.columns([1, 1])
    with g:
        section("Money flow", "accounts and the transfers that fired the rule")
        st.graphviz_chart(money_flow(txns, a.ACCOUNT_ID), use_container_width=True)
    with tl:
        section("Activity timeline", "this account, 30 days around the alert")
        span = q(f"""SELECT TXN_ID, TXN_TS, AMOUNT, DIRECTION FROM {DB}.ACCOUNT_ACTIVITY
                     WHERE ACCOUNT_ID = ? AND TXN_TS BETWEEN DATEADD('day', -21, ?) AND DATEADD('day', 9, ?)""",
                 [a.ACCOUNT_ID, str(txns.TXN_TS.min()), str(txns.TXN_TS.max())])
        span["in_alert"] = span.TXN_ID.isin(txns.TXN_ID).map({True: "in this alert", False: "other activity"})
        st.altair_chart(alt.Chart(span).mark_circle(opacity=.85).encode(
            x=alt.X("TXN_TS:T", title=None), y=alt.Y("AMOUNT:Q", title="USD"),
            size=alt.Size("AMOUNT:Q", legend=None, scale=alt.Scale(range=[40, 400])),
            color=alt.Color("in_alert:N", scale=alt.Scale(domain=["in this alert", "other activity"],
                                                         range=[sev_c, "#8B8D98"]),
                            legend=alt.Legend(orient="bottom", title=None)),
            tooltip=["TXN_ID", "TXN_TS", "AMOUNT", "DIRECTION"]).properties(height=260),
            use_container_width=True)

    section("Transactions that fired the rule")
    st.dataframe(txns, hide_index=True, use_container_width=True,
                 column_config={"AMOUNT": st.column_config.NumberColumn("Amount", format="$%.2f"),
                                "TXN_TS": st.column_config.DatetimeColumn("Time", format="YYYY-MM-DD HH:mm")})
    left, right = st.columns(2)
    with left:
        section("Customer (KYC)")
        st.dataframe(q(f"""SELECT p.* FROM {DB}.CUSTOMER_PROFILE p
                           JOIN {DB}.ACCOUNTS a ON a.CUSTOMER_ID = p.CUSTOMER_ID
                           WHERE a.ACCOUNT_ID = ?""", [a.ACCOUNT_ID]).T.astype(str).set_axis(["value"], axis=1),
                     use_container_width=True)
    with right:
        section("Linked alerts", "this account or its counterparties")
        st.dataframe(q(f"""SELECT ALERT_ID, RULE, ACCOUNT_ID, EFFECTIVE_SEVERITY, STATUS FROM {DB}.ALERT_QUEUE
                           WHERE ALERT_ID <> ? AND ACCOUNT_ID IN (
                             SELECT FROM_ACCOUNT_ID FROM {DB}.TRANSACTIONS t JOIN {DB}.ALERT_TXNS x
                               ON x.TXN_ID = t.TXN_ID WHERE x.ALERT_ID = ?
                             UNION SELECT TO_ACCOUNT_ID FROM {DB}.TRANSACTIONS t JOIN {DB}.ALERT_TXNS x
                               ON x.TXN_ID = t.TXN_ID WHERE x.ALERT_ID = ?)""",
                       [alert_id, alert_id, alert_id]), hide_index=True, use_container_width=True)

    section("Decision", "recorded append-only with your name and role")
    if not CAN_DECIDE:
        st.info("Read-only role: decisions are recorded by analysts.")
    elif a.STATUS != "OPEN":
        st.success(f"Already {a.STATUS}. Decisions are append-only; see 📊 Audit.")
    else:
        with st.form("decide"):
            analyst = st.text_input("Analyst name")
            decision = st.radio("Decision", ["ESCALATE", "DISMISS"], horizontal=True)
            reason = st.text_area("Reason (required: what in the evidence supports this)")
            if st.form_submit_button("Record decision", type="primary"):
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
    text, tables, sqls, tools = [], [], [], []
    for c in resp.get("content", []):
        t = c.get("type")
        if t == "text":
            text.append(c["text"])
        elif t == "table":
            rs = c["table"]["result_set"]
            cols = [r["name"] for r in rs["resultSetMetaData"]["rowType"]]
            tables.append(pd.DataFrame(rs["data"], columns=cols))
        elif t == "tool_use":
            name = c["tool_use"].get("name", "")
            if not name.startswith("system_") and name not in tools:
                tools.append(name)
        elif t == "tool_result":
            for item in c["tool_result"].get("content", []):
                if (item.get("json") or {}).get("sql"):
                    sqls.append(item["json"]["sql"])
    return "\n".join(text).strip(), tables, sqls, tools


SUGGESTED = ["How many open alerts are there by rule and severity?",
             "Which 3 high-risk customers deposited the most cash?",
             "What does our policy say about structuring?",
             "Why was alert 37 raised, and why is it CRITICAL?"]
TOOL_LABEL = {"AmlData": "📊 data · Cortex Analyst", "AmlPolicy": "📚 policy · Cortex Search"}

with tab_ask:
    st.markdown('<div class="muted">Cortex Agent over the semantic view (data, as governed SQL) and '
                'Cortex Search (policy and FinCEN guidance). It explains; it never decides. '
                'Answers take 20–50 seconds.</div>', unsafe_allow_html=True)
    st.session_state.setdefault("chat", [])
    if not st.session_state.chat:
        section("Try asking")
        sc = st.columns(len(SUGGESTED))
        for i, sq in enumerate(SUGGESTED):
            if sc[i].button(sq, key=f"sq{i}", use_container_width=True):
                st.session_state.pending = sq
    for m in st.session_state.chat:
        with st.chat_message(m["role"]):
            if m.get("tools"):
                st.markdown(" ".join(pill(TOOL_LABEL.get(t, t), "#3E63DD") for t in m["tools"]),
                            unsafe_allow_html=True)
            st.markdown(md(m["text"]))
            for t in m.get("tables", []):
                st.dataframe(t, hide_index=True)
            for s in m.get("sqls", []):
                with st.expander("SQL generated by Cortex Analyst"):
                    st.code(s, language="sql")
    prompt = st.chat_input("e.g. Which high-risk customers deposited the most cash?") or st.session_state.pop("pending", None)
    if prompt:
        st.session_state.chat.append({"role": "user", "text": prompt})
        history = [{"role": m["role"], "content": [{"type": "text", "text": m["text"]}]}
                   for m in st.session_state.chat]
        with st.spinner("Asking the copilot… (Cortex Agent is choosing tools)"):
            text, tables, sqls, tools = ask_agent(history)
        st.session_state.chat.append({"role": "assistant", "text": text or "_(no answer)_",
                                      "tables": tables, "sqls": sqls, "tools": tools})
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
        section("Escalated, awaiting a SAR draft")
        pc = st.columns([3, 1])
        pick = pc[0].selectbox("Alert", pending.ALERT_ID.tolist(), key="sarpick",
                               format_func=lambda x: f"{x} · " + RULE_LABEL[pending.set_index('ALERT_ID').loc[x, 'RULE']])
        pc[1].markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
        if pc[1].button("Draft SAR with Cortex AI", type="primary", use_container_width=True):
            with st.spinner("Drafting from the evidence with claude-sonnet-4-5…"):
                draft_sar(pick)
            st.rerun()
    elif CAN_DECIDE:
        st.info("No escalated alerts waiting. A SAR can only be drafted after a human escalates.")

    sars = q(f"""SELECT s.SAR_ID, s.ALERT_ID, s.STATUS, s.NARRATIVE, s.DRAFTED_BY, s.CREATED_AT,
                        c.TXNS_CITED, c.VERIFIED, c.UNVERIFIED
                 FROM {DB}.SAR_REVIEW s LEFT JOIN {DB}.SAR_CITATION_CHECK c ON c.SAR_ID = s.SAR_ID
                 ORDER BY s.SAR_ID DESC""")
    section("Reports", f"{len(sars)} total")
    for _, s in sars.iterrows():
        unverified = [x for x in json.loads(s.UNVERIFIED or "[]") if x]
        ok = not unverified
        with st.container(border=True):
            st.markdown(f"**SAR {s.SAR_ID}** · alert {s.ALERT_ID} &nbsp; {pill(s.STATUS, STATUS_COLOR.get(s.STATUS, '#8B8D98'))}"
                        f"{pill(f'citations {int(s.VERIFIED or 0)}/{int(s.TXNS_CITED or 0)} verified', '#30A46C' if ok else '#E5484D')}",
                        unsafe_allow_html=True)
            with st.expander("Narrative", expanded=s.STATUS == "DRAFT"):
                st.text(s.NARRATIVE)
            st.caption(f"Drafted by {s.DRAFTED_BY} · {s.CREATED_AT}")
            if unverified:
                st.error(f"Unverified transaction IDs: {', '.join(unverified)}. Cannot be approved.")
            if CAN_APPROVE and s.STATUS == "DRAFT":
                if st.button("Approve for filing", key=f"approve{s.SAR_ID}", disabled=not ok, type="primary"):
                    session.sql(f"UPDATE {DB}.SAR_REPORTS SET STATUS = 'APPROVED' WHERE SAR_ID = ?",
                                params=[int(s.SAR_ID)]).collect()
                    st.rerun()
            elif s.STATUS == "DRAFT":
                st.caption("Approval requires the COMPLIANCE_OFFICER role.")


# ---------------------------------------------------------------- audit
with tab_audit:
    metrics = q(f"""SELECT RULE, ALERTS, PRECISION, LABELLED_CASES, CAUGHT_CASES, CASE_RECALL
                    FROM {DB}.RULE_METRICS ORDER BY RULE""")
    caught, labelled = int(metrics.CAUGHT_CASES.sum()), int(metrics.LABELLED_CASES.sum())
    c = st.columns(4)
    kpi(c[0], "Schemes caught", f"{caught} / {labelled}", "planted fraud cases (ground truth)", "#30A46C")
    kpi(c[1], "Rules", len(metrics), "deterministic, policy-mapped")
    kpi(c[2], "Precision", f"{metrics.PRECISION.astype(float).min():.2f}–{metrics.PRECISION.astype(float).max():.2f}",
        "alerts that hit real fraud", "#3E63DD")
    kpi(c[3], "Decisions logged", int(q(f"SELECT COUNT(*) N FROM {DB}.FINDINGS")["N"][0]), "append-only")

    section("Rule performance", "against seeded ground truth — the rules never read the labels")
    long = metrics.melt(id_vars="RULE", value_vars=["PRECISION", "CASE_RECALL"], var_name="metric")
    long["value"] = long["value"].astype(float)
    long["RULE"] = long["RULE"].map(RULE_LABEL)
    long["metric"] = long["metric"].map({"PRECISION": "Precision", "CASE_RECALL": "Cases caught"})
    st.altair_chart(alt.Chart(long).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
        x=alt.X("RULE:N", title=None, axis=alt.Axis(labelAngle=0)), xOffset="metric:N",
        y=alt.Y("value:Q", title=None, scale=alt.Scale(domain=[0, 1])),
        color=alt.Color("metric:N", scale=alt.Scale(range=["#3E63DD", "#30A46C"]),
                        legend=alt.Legend(orient="bottom", title=None)),
        tooltip=["RULE", "metric", alt.Tooltip("value:Q", format=".2f")]).properties(height=260),
        use_container_width=True)
    st.dataframe(metrics, hide_index=True, use_container_width=True)

    section("Decision log", "append-only: no role can update or delete a finding")
    st.dataframe(q(f"""SELECT FINDING_ID, ALERT_ID, DECISION, ANALYST, REASON, POLICY_REFS,
                              DECIDED_BY_ROLE, DECIDED_AT FROM {DB}.FINDINGS ORDER BY FINDING_ID DESC"""),
                 hide_index=True, use_container_width=True)

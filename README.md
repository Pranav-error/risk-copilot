# Risk Copilot — Fraud, AML & Regulatory Intelligence on Snowflake

Snowflake CoCo CLI Hackathon 2026 (GCC Edition) — Problem Statement #1:
*"Build a copilot that surfaces risk and fraud signals and produces audit-ready regulatory
outputs from natural language questions."*

**Team cypher:** Sai Pranav · Udith

---

## 1. The problem

An AML analyst at a GCC risk-ops centre supporting a global bank works an alert like this:

1. A monitoring system flags an account.
2. The analyst pulls its transactions from one system, the customer's KYC profile from another.
3. They look up the bank's AML policy and the FinCEN / FATF guidance to decide whether the
   activity actually breaches anything.
4. They write up a decision, and if it is suspicious, a Suspicious Activity Report (SAR).
5. Months later an auditor asks *"why did you decide that?"*, and every step must be traceable.

That takes hours per alert, most alerts are false positives, and the reasoning usually lives
in someone's head or a spreadsheet.

**Risk Copilot runs the whole flow inside Snowflake, driven from the CoCo CLI:**

```
   SIGNAL            →        EVIDENCE           →      FINDING          →      REPORT
 deterministic rules     transactions + KYC +       analyst decision,      SAR draft citing
 raise an alert          cited policy clauses       append-only log        every fact it uses
```

### Who uses it

| Persona | What they do here |
|---|---|
| **AML analyst** (daily user) | Works the alert queue, investigates, escalates or dismisses |
| **Compliance officer** | Reviews escalations, approves and files SARs |
| **Auditor** | Read-only: sees every decision and its evidence, with customer PII masked |
| **Risk / compliance lead** (buyer) | Rule performance, alert volumes, time to disposition |

---

## 2. Design principles

1. **The LLM never decides what is fraud.** Detection is deterministic SQL and Snowpark, written
   down in policy clauses. The AI explains evidence and drafts text; a named human decides.
2. **Every claim cites its source.** A transaction ID, or a policy / regulation clause retrieved
   by Cortex Search. A statement with no citation is not allowed in an answer or a SAR.
3. **Decisions are append-only.** Analyst dispositions go to a `FINDINGS` log that the analyst
   role can insert into but never update or delete.
4. **Measured, not claimed.** Every rule is scored for precision and recall against hidden
   ground-truth labels that the rules themselves never read.
5. **Anything the AI explains is stored as data first.** Severity reasons, policy clauses and
   evidence numbers are columns, not inferences. Every time a fact was left implicit in testing,
   the model filled the gap with a plausible wrong answer.
6. **Everything stays in Snowflake.** Data, detection, retrieval, AI, governance and the app
   run in one account. Nothing leaves it.

---

## 3. High-level architecture

```mermaid
flowchart TB
    U["👤 AML analyst · Compliance officer · Auditor"]

    subgraph IF["Interfaces"]
        direction LR
        COCO["CoCo CLI skills<br/>$aml-detect · $aml-investigate · $sar-draft"]
        APP["Streamlit command centre<br/>planned"]
    end

    subgraph AI["AI & evidence — explains, never decides"]
        direction LR
        CSS["Cortex Search<br/>policy + FinCEN + FATF clauses"]
        AGT["Cortex Agent + Analyst<br/>semantic view · planned"]
    end

    subgraph FND["Findings — audit trail"]
        direction LR
        FIN["FINDINGS<br/>append-only analyst decisions"]
        SAR["SAR_REPORTS<br/>cited drafts"]
    end

    subgraph DET["Detection — deterministic"]
        direction LR
        SQLR["SQL rules<br/>structuring · velocity · pass-through · geo-risk"]
        CYC["Snowpark proc<br/>round-trip cycles"]
        ALR["ALERTS → ALERT_QUEUE"]
    end

    subgraph DATA["Data"]
        direction LR
        RAW["RAW_* tables<br/>generated CSVs"]
        CAN["CUSTOMERS · ACCOUNTS<br/>TRANSACTIONS"]
        REF["FATF_JURISDICTIONS"]
    end

    GOV["🔒 Governance — masking policies · roles · append-only grants · planned"]

    U --> IF
    IF --> AI
    IF --> FND
    AI --> DET
    FND --> DET
    DET --> DATA
    RAW --> CAN
    SQLR --> ALR
    CYC --> ALR
    GOV -.- DATA
    GOV -.- FND
```

Requests flow top to bottom; every layer runs inside one Snowflake account.

| Layer | Snowflake features | Status |
|---|---|---|
| 1 · Data | Stages, `COPY INTO`, tables, file formats | ✅ Live on Snowflake |
| 2 · Detection | SQL, Snowpark Python stored procedure | ✅ Live on Snowflake |
| 3 · Evidence | Cortex Search, `AI_PARSE_DOCUMENT`, `SPLIT_TEXT_MARKDOWN_HEADER` | ✅ Live (119 chunks, 3 documents) |
| 3 · Evidence | Semantic view, Cortex Analyst, Cortex Agent | Planned |
| 4 · Finding | Tables + role grants (append-only) | ✅ Tables live; grants planned |
| 5 · Governance | Masking policies, RBAC roles, tags | Planned |
| App | Streamlit in Snowflake | Planned |
| Interface | CoCo CLI project skills (`.cortex/skills/`) | ✅ `$aml-detect` verified in CoCo v1.1.87 |

---

## 4. Pipelines

### 4.1 Data pipeline — raw files to the data contract

```mermaid
flowchart LR
    G["Synthetic generator<br/>Udith"] --> CSV["data/*.csv"]
    CSV -->|"PUT @RAW_DATA"| STG["Stage RAW_DATA"]
    STG -->|"COPY INTO<br/>01_load_raw.sql"| RAW["RAW_CUSTOMERS<br/>RAW_ACCOUNTS<br/>RAW_TRANSACTIONS<br/>RAW_LABELS<br/>RAW_HIGH_RISK_COUNTRIES"]
    RAW -->|"02_canonical.sql"| CAN["CUSTOMERS<br/>ACCOUNTS<br/>TRANSACTIONS<br/>TXN_LABELS"]
    RAW -->|"05_reference.sql"| REF["FATF_JURISDICTIONS<br/>FATF lists + bank's own list"]
```

Raw files are loaded **unchanged**; one SQL step maps them onto the contract every other
component depends on. A new data source only needs a new mapping, never a rule change.

What `02_canonical.sql` does:

| Raw shape | Canonical shape | Rule |
|---|---|---|
| One-sided row: `account_id` + `counterparty_account` | `FROM_ACCOUNT_ID` → `TO_ACCOUNT_ID` | `deposit` or "incoming…" description = inflow; otherwise outflow. External side is `NULL` |
| `txn_type` + `channel` + description | `CHANNEL` = CASH / WIRE / INTERNAL / UPI / ACH | "cash…" description, or deposit/withdrawal at ATM/branch = CASH |
| `kyc_risk_rating`, `expected_monthly_volume` | `RISK_RATING`, `DECLARED_MONTHLY_INCOME` | Upper-cased; volume is the §7.3 income baseline |
| Generator typology names | `TYPOLOGY` | `RAPID_LAYERING` → `LAYERING`, `ROUND_TRIPPING` → `ROUND_TRIP`, … |
| `currency = INR` | `CURRENCY = USD` | Typologies are sized for US BSA thresholds; see `data/README.md` |

### 4.2 Detection pipeline — transactions to alerts

```mermaid
flowchart LR
    T["TRANSACTIONS"] --> R1["§3.3 STRUCTURING<br/>SQL"]
    T --> R2["§4.1 VELOCITY<br/>SQL"]
    T --> R3["§5.5 PASS_THROUGH<br/>SQL"]
    T --> R4["§6.2 GEO_RISK<br/>SQL"]
    F["FATF_JURISDICTIONS"] --> R4
    T --> R5["§5.2 ROUND_TRIP_CYCLE<br/>Snowpark: cycles.py"]
    R1 --> AL["ALERTS<br/>rule · account · txn ids · severity · evidence JSON"]
    R2 --> AL
    R3 --> AL
    R4 --> AL
    R5 --> AL
    AL --> Q["ALERT_QUEUE view<br/>§7.2 high-risk customers +1 severity"]
    AL --> X["ALERT_TXNS view<br/>one row per alert × txn"]
    L["TXN_LABELS<br/>ground truth"] -.->|"evaluation only"| M["RULE_METRICS<br/>30_evaluate.sql"]
    X --> M
```

| Rule | Policy | Logic | Severity |
|---|---|---|---|
| `STRUCTURING` | §3.3 | ≥3 cash deposits of $8,000–$9,999 within 7 days, total > $10,000 | HIGH |
| `VELOCITY` | §4.1 | ≥4 transactions in any rolling 24h, value > 5× trailing 90-day daily average, min $10,000 | MEDIUM; HIGH if no prior activity |
| `PASS_THROUGH` | §5.5 | Inflow ≥ $50,000, then ≥2 outflows totalling ≥50% of it within 72h | HIGH |
| `GEO_RISK` | §6.2 | Any wire with a FATF black-list country; grey-list wires > $50,000 in 30 days | CRITICAL / HIGH |
| `ROUND_TRIP_CYCLE` | §5.2 | Money leaves and returns through 2–4 time-ordered hops within 7 days, each hop ≥80% of the last | MEDIUM (2 hops) / HIGH (3+) |

Properties every rule shares:
- **Stable on rerun:** rules write candidates; only alerts with a new key
  (`MD5(rule | sorted txn ids)`) are inserted. Rerunning never renumbers an alert or resets its
  status, so `FINDINGS` and `SAR_REPORTS` always point at the alert the analyst reviewed.
- **Deterministic:** every "keep the best window" breaks ties by earliest time, then txn id.
- **Explainable:** `EVIDENCE` stores the numbers that fired it (totals, multiples, window, policy clause).
- **Traceable:** `TXN_IDS` stores the exact transactions, so every alert links to raw evidence.
- **Severity bump is a view, not an update:** reruns never escalate the same alert twice, and
  `ALERT_QUEUE.SEVERITY_REASON` states why in words the AI repeats verbatim.

**Why the cycle rule is Python, not SQL.** Finding A→B→C→A where each transfer happens *after*
the previous one is a graph search with time constraints. `detection/cycles.py` runs a bounded
depth-first search over time-ordered edges. The idea comes from Pranav's Arbix project
(Bellman-Ford cycle detection), but Bellman-Ford ignores time order and would report loops
money could not actually have travelled. It runs as a Snowpark stored procedure, so data never
leaves Snowflake.

### 4.3 Evidence pipeline — documents to citations

```mermaid
flowchart LR
    P["internal_aml_policy.pdf<br/>FinCEN SAR guidance<br/>FATF reports"] -->|"PUT"| S["Stage REG_DOCS"]
    S -->|"AI_PARSE_DOCUMENT<br/>LAYOUT mode"| RT["REG_DOCS_RAW"]
    RT -->|"SPLIT_TEXT_MARKDOWN_HEADER<br/>2000 chars, 300 overlap"| CH["REG_DOC_CHUNKS<br/>source · section · chunk"]
    CH --> CS["Cortex Search service<br/>AML_POLICY_SEARCH"]
    CS -->|"SEARCH_PREVIEW"| Q["Skills / Agent<br/>cite source + section"]
```

Chunks keep their **source file and section header**, so an answer can cite
*"internal_aml_policy.pdf, §3 Structuring"* rather than paste anonymous text.

The internal policy (`corpus/internal_aml_policy.md`) is a fictional bank's monitoring policy
written for this project. Every detection rule is a numbered clause in it, which is what
lets the copilot say *which rule* an account broke and *where that rule is written down*.

---

## 5. The analyst process — signal to SAR

```mermaid
sequenceDiagram
    autonumber
    actor An as AML analyst
    participant Co as CoCo CLI
    participant Sf as Snowflake
    participant Cs as Cortex Search
    actor Of as Compliance officer

    An->>Co: $aml-detect
    Co->>Sf: run rules + DETECT_ROUND_TRIPS
    Sf-->>Co: ALERT_QUEUE by rule and severity
    Co-->>An: top 5 alerts with policy clause

    An->>Co: $aml-investigate 42
    Co->>Sf: alert, its transactions, customer KYC, linked alerts
    Co->>Cs: search policy + FinCEN/FATF for this pattern
    Cs-->>Co: clauses with source and section
    Co-->>An: explanation, every claim cited
    An->>Co: escalate, reason "no business purpose for intermediaries"
    Co->>Sf: INSERT INTO FINDINGS, ALERTS.STATUS = ESCALATED

    An->>Co: $sar-draft 42
    Co->>Sf: check alert was escalated by a human
    Co->>Cs: FinCEN narrative guidance
    Co->>Sf: INSERT INTO SAR_REPORTS status DRAFT
    Co-->>An: who / what / when / where / why, cited
    Of->>Sf: review and approve the draft
```

| Step | Who decides | What is recorded |
|---|---|---|
| Alert raised | A deterministic rule | `ALERTS`: rule, txn ids, evidence numbers, policy clause |
| Investigation | Nobody: evidence is gathered and explained | Nothing new, read-only |
| Disposition | The **analyst**, by name, with a reason | `FINDINGS`: decision, analyst, reason, evidence txns, clauses, role, timestamp |
| SAR draft | The AI drafts, **only after** a human escalation | `SAR_REPORTS`: narrative, cited txns, cited sources, status `DRAFT` |
| Filing | The **compliance officer** | `SAR_REPORTS.STATUS` → `APPROVED` / `FILED` |

### CoCo skills

Project skills live in `.cortex/skills/` and are called as `$skill-name`
([CoCo extensibility docs](https://docs.snowflake.com/en/user-guide/cortex-code/extensibility)).

| Skill | Input | Does | Writes |
|---|---|---|---|
| `$aml-detect` | — | Runs all rules + cycle proc; summarises queue; shows precision/recall on request | `ALERTS` |
| `$aml-investigate` | `ALERT_ID` | Pulls txns, KYC, linked alerts, policy + regulation clauses; explains with citations; records the analyst's decision | `FINDINGS`, `ALERTS.STATUS` |
| `$sar-draft` | escalated `ALERT_ID` | Refuses unless a human escalated it; drafts a FinCEN-structured narrative with a citation on every claim; no legal conclusions; runs the citation check | `SAR_REPORTS` |

**SAR citation check.** `SAR_CITATION_CHECK` extracts every `TXN_…` ID from a narrative and
verifies it exists and belongs to the case. Tested against a planted bad draft: it flagged a
made-up ID and a real transaction from another customer, and passed the genuine one.

---

## 6. Data model

```mermaid
erDiagram
    CUSTOMERS ||--o{ ACCOUNTS : owns
    ACCOUNTS ||--o{ TRANSACTIONS : "sends / receives"
    TRANSACTIONS ||--o| TXN_LABELS : "ground truth"
    ACCOUNTS ||--o{ ALERTS : raises
    ALERTS }o--o{ TRANSACTIONS : "TXN_IDS evidence"
    ALERTS ||--o{ FINDINGS : "decided in"
    ALERTS ||--o{ SAR_REPORTS : "reported in"
    FATF_JURISDICTIONS ||--o{ TRANSACTIONS : "COUNTERPARTY_COUNTRY"

    CUSTOMERS {
        string CUSTOMER_ID PK
        string NAME
        string RISK_RATING
        boolean PEP_FLAG
        number DECLARED_MONTHLY_INCOME
    }
    ACCOUNTS {
        string ACCOUNT_ID PK
        string CUSTOMER_ID FK
        string ACCOUNT_TYPE
    }
    TRANSACTIONS {
        string TXN_ID PK
        string FROM_ACCOUNT_ID "NULL = external"
        string TO_ACCOUNT_ID "NULL = external"
        number AMOUNT
        string CHANNEL
        string COUNTERPARTY_COUNTRY
        timestamp TXN_TS
    }
    ALERTS {
        number ALERT_ID PK
        string RULE
        string ACCOUNT_ID
        array TXN_IDS
        string SEVERITY
        variant EVIDENCE
        string STATUS
    }
    FINDINGS {
        number FINDING_ID PK
        number ALERT_ID FK
        string DECISION
        string ANALYST
        string REASON
        array POLICY_REFS
        timestamp DECIDED_AT
    }
    SAR_REPORTS {
        number SAR_ID PK
        number ALERT_ID FK
        string STATUS
        string NARRATIVE
        array CITED_TXN_IDS
        array CITED_SOURCES
    }
```

### Data contract

All detection, the semantic model and the app depend on these names. All amounts in **USD**.

```
CUSTOMERS    (CUSTOMER_ID, NAME, CUSTOMER_TYPE, RISK_RATING, PEP_FLAG, OCCUPATION,
              DECLARED_MONTHLY_INCOME, COUNTRY, ONBOARDED_AT)
ACCOUNTS     (ACCOUNT_ID, CUSTOMER_ID, ACCOUNT_TYPE, OPENED_AT, STATUS)
TRANSACTIONS (TXN_ID, FROM_ACCOUNT_ID, TO_ACCOUNT_ID, COUNTERPARTY_REF, AMOUNT, CURRENCY,
              CHANNEL, ACCESS_POINT, COUNTERPARTY_COUNTRY, TXN_TS, DESCRIPTION)
             -- FROM/TO = internal ACCOUNT_ID, or NULL for the external side
             -- cash deposit = FROM NULL, TO the account, CHANNEL 'CASH'
             -- CHANNEL in (CASH, WIRE, ACH, UPI, INTERNAL)
             -- COUNTERPARTY_COUNTRY = ISO-2 code, matches FATF_JURISDICTIONS
TXN_LABELS   (TXN_ID, ACCOUNT_ID, TYPOLOGY)  -- ground truth; detection never reads it
```

---

## 7. Governance (planned)

| Control | Implementation | Shows in the demo as |
|---|---|---|
| PII masking | Masking policy on `CUSTOMERS.NAME`, account IDs; tag-based | Auditor role sees `***MASKED***` |
| Least privilege | Roles `AML_ANALYST`, `COMPLIANCE_OFFICER`, `AUDITOR` | Switching role changes what is visible |
| Append-only decisions | `AML_ANALYST` gets `INSERT, SELECT` on `FINDINGS`, never `UPDATE/DELETE` | A decision cannot be rewritten |
| Ground-truth isolation | No detection role can read `TXN_LABELS` | Evaluation is honest |
| Point-in-time evidence | Time Travel on `TRANSACTIONS`, `ALERTS` | What the analyst saw when they decided |

---

## 8. Evaluation

The generator plants known typologies and records them in `TXN_LABELS`. Only
`sql/30_evaluate.sql` reads that table. `RULE_METRICS` reports, per rule:

| Metric | Meaning |
|---|---|
| `PRECISION` | Alerts that contain at least one truly suspicious transaction / all alerts |
| `TYPOLOGY_PRECISION` | Same, but the label must match the rule's own typology. Lower for velocity by design: a spike on a cycle or FATF account is a real catch under another name |
| `CASE_RECALL` | Labelled accounts with at least one transaction in a matching alert / labelled accounts. **The headline number:** one alert per scheme is enough for an analyst |
| `RECALL` | Same at transaction level. Understates bursts longer than the rule window |

### Results on `data/` (41,492 transactions, 168 labelled)

| Rule | Alerts | Precision | Cases caught |
|---|---|---|---|
| STRUCTURING | 12 | 1.00 | 12 / 12 |
| VELOCITY | 32 | 0.75 | 12 / 12 |
| PASS_THROUGH | 14 | 0.93 | 10 / 11 |
| ROUND_TRIP_CYCLE | 4 | 1.00 | all 4 cycles (12 accounts) |
| GEO_RISK | 6 | 1.00 | 6 / 6 |

Identical on Snowflake and locally: `SELECT * FROM RISK_COPILOT.AML.RULE_METRICS` returns
exactly the table the DuckDB harness prints.

The one miss is a layering account that passed on 47.9% of its inflow, under the 50% rule
threshold. **Caveat:** velocity and pass-through thresholds were calibrated on this file, so
these numbers are optimistic. Final numbers come from a second, differently seeded file that
no threshold was tuned against.

---

## 9. Running it

### 9.1 One-time setup

1. **Snowflake account.** Sign up on the *Snowflake CoCo – For Developers* tab at
   signup.snowflake.com (includes $40 CoCo + $360 platform credits). Our build uses an
   AWS **ap-northeast-1 (Tokyo)** trial account.
2. **CoCo CLI** (macOS / Linux):
   ```
   curl -LsS https://ai.snowflake.com/static/cc-scripts/install.sh | sh     # installs ~/.local/bin/cortex
   cortex --version                                                         # built on v1.1.87
   ```
3. **Connect.** Run `cortex` once; the wizard creates `~/.snowflake/connections.toml` with
   browser (OAuth) login. Then make sure the connection has a warehouse and our schema:
   ```toml
   default_connection_name = "<your connection>"

   [<your connection>]
   account = "<account>.<region>.aws"
   user = "<USER>"
   authenticator = "OAUTH_AUTHORIZATION_CODE"
   role = "ACCOUNTADMIN"
   warehouse = "COMPUTE_WH"
   database = "RISK_COPILOT"
   schema = "AML"
   ```
   If the wizard shows an *Agent connection* and a *SQL connection*, both must be the same
   account, or tables land in one account and AI usage bills another.

### 9.2 Deploy to Snowflake

One command uploads the files and runs every script, in order, into `RISK_COPILOT.AML`:

```
pip install "snowflake-connector-python[secure-local-storage]"
python3 scripts/deploy.py                      # default connection from ~/.snowflake/connections.toml
python3 scripts/deploy.py --only 20_detection_rules.sql 30_evaluate.sql   # rerun part of it
```

Order: `01_load_raw → 02_canonical → 05_reference → 10_alerts_and_cycle_proc → 20_detection_rules
→ 30_evaluate → 50_findings → 40_cortex_search`. Every script is idempotent.

What it creates:

| Kind | Objects |
|---|---|
| Stages | `RAW_DATA`, `CODE_STAGE`, `REG_DOCS` |
| Raw tables | `RAW_CUSTOMERS`, `RAW_ACCOUNTS`, `RAW_TRANSACTIONS`, `RAW_LABELS`, `RAW_HIGH_RISK_COUNTRIES` |
| Contract tables | `CUSTOMERS`, `ACCOUNTS`, `TRANSACTIONS`, `TXN_LABELS`, `FATF_JURISDICTIONS` |
| Detection | `ALERTS`, `ALERT_CANDIDATES`, procedure `DETECT_ROUND_TRIPS()`, views `ALERT_QUEUE`, `ALERT_TXNS` |
| Evaluation | views `RULE_TYPOLOGY`, `RULE_METRICS` |
| Evidence | `REG_DOCS_RAW`, `REG_DOC_CHUNKS` (119 chunks), Cortex Search service `AML_POLICY_SEARCH` |
| Findings | `FINDINGS`, `SAR_REPORTS`, view `SAR_CITATION_CHECK` |

### 9.3 Use it from CoCo

```
cd risk-copilot
cortex
> $aml-detect
> $aml-investigate <ALERT_ID>
> $sar-draft <ALERT_ID>
```

CoCo asks for approval before any statement that changes data (`INSERT`, `DELETE`, …). In
the demo that is deliberate: the analyst approves each write. `cortex exec` (non-interactive)
auto-rejects those prompts, so it can only run the read-only parts.

A real `$aml-detect` run on the deployed data (CoCo v1.1.87):

| Rule | Severity | Open alerts |
|---|---|---|
| GEO_RISK | CRITICAL | 2 |
| GEO_RISK | HIGH | 4 |
| PASS_THROUGH | HIGH | 14 |
| ROUND_TRIP_CYCLE | HIGH | 4 |
| STRUCTURING | CRITICAL | 1 |
| STRUCTURING | HIGH | 11 |
| VELOCITY | HIGH | 1 |
| VELOCITY | MEDIUM | 31 |

Top of the queue: two wires to FATF black-list countries (North Korea, Myanmar; $360,934 and
$289,639), then a structuring case raised to CRITICAL because the customer is high-risk (§7.2).

### 9.4 End-to-end test on live Snowflake

Every skill was run through CoCo against the deployed data, and every fact it stated was
checked against the database by query.

| # | Test | Result |
|---|---|---|
| T1 | `$sar-draft` on an alert no human reviewed | ✅ Refused; no SAR written |
| T2 | `$aml-investigate` (structuring, CRITICAL) | ✅ All 4 txn IDs, amounts, times, $38,296.10 total, KYC, linked alert verified by query |
| T3 | Escalate with analyst name + reason | ✅ One `FINDINGS` row (4 evidence txns, §3.3 + §7.2); alert `ESCALATED` |
| T4 | `$sar-draft` after escalation | ✅ `DRAFT` saved; citation check 4 / 4; no legal conclusion; correct severity reason |
| T5 | `$aml-investigate` on a money loop | ✅ Showed the 3-hop loop **and** linked the middle account's own pass-through alert |
| T6 | Dismiss a false-positive velocity alert | ✅ `DISMISSED`, reason recorded |
| T7 | Rerun all detection after a decision | ✅ 68 → 68 alerts, ids/keys/statuses identical (`scripts/check_rerun_stable.py`) |

What testing caught and fixed along the way:

| Found | Fix |
|---|---|
| Cited §7.3 (income) as breached at 1.43×; the clause needs 3× for two months | Skills must show the arithmetic and say "relevant but not met" |
| SAR said the subject acted "in violation of 31 U.S.C. § 5324" | Skills forbid legal conclusions: "appears consistent with" |
| SAR invented "UTC" for timestamps with no timezone | Skills forbid adding a timezone |
| Said severity was raised "because of a linked alert" (it was the customer's risk rating) | `SEVERITY_REASON` column; skills repeat it verbatim |
| **Rerunning detection renumbered every alert**, orphaning findings and SARs | Stable alert keys; regression test escalates, reruns, compares |
| Identical-amount bursts tied; Snowflake picked a different window per run (68 → 72 alerts) | Total-order tie-breaks; test shuffles row order, fails 3/3 without the fix |

### 9.5 Locally, no Snowflake needed

The real SQL files run on DuckDB; only Snowflake-only functions are translated (`tests/duck.py`).

```
pip install duckdb
python3 detection/cycles.py           # cycle detector self-check
python3 tests/test_rules_duckdb.py    # every rule on planted typologies: case recall 1.0, rerun stable
python3 tests/run_on_data.py          # full pipeline on data/: RULE_METRICS + rerun stable
python3 scripts/check_rerun_stable.py # same rerun check, live on Snowflake
```

### 9.6 Gotchas we hit

| Symptom | Cause | Fix |
|---|---|---|
| `DETECT_ROUND_TRIPS` crashed: `Decimal * float` | Snowflake `NUMBER` arrives in Python as `Decimal`; DuckDB gave floats | `find_cycles` casts amounts; the self-check now feeds it `Decimal`s |
| CoCo cited money-loop alerts as §7.2 | Cycle alerts didn't record their clause, so the model guessed | Every rule writes `EVIDENCE:policy`; a query confirms 0 alerts without one |
| Skill said "SQL tool is blocked" | Docs say `sql_execute`; CoCo 1.1.87's tool is `snowflake_sql_execute` | Skills declare `snowflake_sql_execute` (name taken from CoCo's bundled skills) |
| Tables in one account, credits in another | Wizard set Agent and SQL connections to different accounts | One connection only, set as default |
| `cortex sql …` hung | Not a real subcommand; it opened an interactive session | Use `scripts/deploy.py` or the skills |
| `AT` alias failed | Reserved word in DuckDB and Snowflake (Time Travel) | Renamed alias |

---

## 10. Repository layout

| Path | What | Owner |
|---|---|---|
| `data/` | Generated synthetic data + mapping notes (`data/README.md`) | Udith |
| `sql/01_load_raw.sql` | Stage + `COPY INTO` raw tables | Pranav |
| `sql/02_canonical.sql` | Raw → data contract | Pranav |
| `sql/05_reference.sql` | FATF black/grey list + bank's own high-risk list | Pranav |
| `sql/10_alerts_and_cycle_proc.sql` | `ALERTS` table + registers the cycle proc | Pranav |
| `sql/20_detection_rules.sql` | Four SQL rules (structuring, velocity, pass-through, geo-risk), `ALERT_QUEUE`, `ALERT_TXNS` | Pranav |
| `sql/30_evaluate.sql` | `RULE_METRICS` against ground truth | Pranav |
| `sql/40_cortex_search.sql` | Parse, chunk, index the corpus | Pranav |
| `sql/50_findings.sql` | `FINDINGS`, `SAR_REPORTS` | Pranav |
| `detection/cycles.py` | Round-trip / layering cycle search (Snowpark handler) | Pranav |
| `.cortex/skills/` | `aml-detect`, `aml-investigate`, `sar-draft` | Pranav |
| `corpus/` | Policy + FinCEN/FATF documents for Cortex Search | Pranav |
| `tests/` | DuckDB harness, planted-typology test, real-data run | Pranav |
| `scripts/deploy.py` | Uploads files + runs all SQL in order on Snowflake | Pranav |
| `scripts/check_rerun_stable.py` | Live check: reruns never change alerts | Pranav |
| `docs/brief.txt` | Submission brief (≤1024 characters) | both |

### Corpus sources

| File | Source |
|---|---|
| `internal_aml_policy.md` / `.pdf` | Written for this project (fictional bank). The PDF is what gets indexed |
| `fincen_sar_narrative_guidance.pdf` | FinCEN, *Guidance on Preparing a Complete & Sufficient SAR Narrative* |
| `fincen_sar_filing_instructions.pdf` | FinCEN, SAR Electronic Filing Instructions |
| *to add by hand* | FATF Recommendations; current FATF high-risk & monitored jurisdictions; FATF *Professional Money Laundering* (fatf-gafi.org blocks scripted downloads) |

---

## 11. Status and roadmap

Submission closes **4 Oct 2026, 11:59 PM IST**. Required: public GitHub repo, deployed link,
≤1024-character brief, 3–5 minute demo video **recorded in CoCo CLI** (input → processing →
output, 2–3 modular skills), and a PDF deck (≤5 MB) on the hackathon template.

| | Item | Owner |
|---|---|---|
| ✅ | Data load + canonical mapping on the real generated data | Udith · Pranav |
| ✅ | Five detection rules + cycle search, evaluated against ground truth | Pranav |
| ✅ | Deployed to Snowflake (`RISK_COPILOT.AML`); metrics match the local run exactly | Pranav |
| ✅ | Cortex Search over policy + FinCEN docs (119 chunks) | Pranav |
| ✅ | `$aml-detect` runs end to end in CoCo | Pranav |
| ✅ | All three skills tested end to end on live alerts (section 9.4) | Pranav |
| ⏳ | Semantic view + Cortex Analyst + Cortex Agent (natural-language questions) | Udith |
| ⏳ | Masking policies + `AML_ANALYST` / `COMPLIANCE_OFFICER` / `AUDITOR` roles + append-only grants | Udith |
| ⏳ | Streamlit command centre (the deployed link) | Pranav |
| ⏳ | Second, differently seeded dataset for held-out evaluation | Udith |
| ⏳ | FATF PDFs into `corpus/`; refresh `FATF_JURISDICTIONS` from the current list | Udith |
| ⏳ | Deck on the hackathon template, demo video, repo made public | both |

**Beyond the hackathon:** case-management integration, analyst feedback feeding threshold
tuning, regulator-format export (FinCEN BSA XML), RBI / FIU-IND STR as a second jurisdiction,
ML risk scoring behind the deterministic rules (explainable boosting, never a black box).

---

*Synthetic data only. No real customers, accounts or personal data. AI-generated
explanations and drafts are decision support; filing decisions rest with named humans.*

# Northbridge Bank — Internal AML Transaction Monitoring Policy (Synthetic)

> Fictional institution. Written for the Risk Copilot demo; indexed by Cortex Search
> so every alert explanation can cite a clause. Thresholds mirror US BSA/FinCEN rules.

**Version 3.2 · Owner: Financial Crimes Compliance · Approved by: Chief Risk Officer**

## 1. Purpose and scope
1.1 This policy sets the rules Northbridge Bank uses to detect, investigate and report suspicious activity under the Bank Secrecy Act, FinCEN regulations and FATF Recommendations.
1.2 It applies to all retail and business accounts and all channels: cash, wire, ACH, card and internal transfer.

## 2. Roles
2.1 **AML Analyst** reviews alerts, gathers evidence and records a disposition (Escalate or Dismiss) with a written reason.
2.2 **Compliance Officer** reviews escalated alerts and decides whether to file a Suspicious Activity Report (SAR).
2.3 **Auditor** has read-only access to alerts, dispositions and SARs. Customer identifiers are masked for this role.
2.4 No alert may be closed without a recorded disposition. Dispositions are append-only and may not be edited after submission.

## 3. Structuring
3.1 Cash transactions over **$10,000** in a business day require a Currency Transaction Report (CTR).
3.2 Structuring is breaking cash activity into amounts below $10,000 to avoid a CTR. It is a federal offence (31 U.S.C. § 5324) regardless of the source of funds.
3.3 **Rule STRUCTURING:** raise an alert when an account makes **3 or more cash deposits between $8,000 and $9,999 within 7 days** whose total exceeds $10,000.
3.4 Severity is HIGH; CRITICAL if the pattern repeats across 2 or more linked accounts.

## 4. Velocity anomalies
4.1 **Rule VELOCITY:** raise an alert when an account's transaction count or value in any 24-hour period exceeds **5 times its trailing 90-day daily average**, with a minimum of $25,000.
4.2 Severity is MEDIUM; HIGH if the account is less than 90 days old or was dormant (no activity for 180+ days) before the spike.

## 5. Layering and round-tripping
5.1 Layering is moving funds through multiple accounts to obscure their origin. Round-tripping is funds returning to the originating account through intermediaries.
5.2 **Rule ROUND_TRIP_CYCLE:** raise an alert when funds of **$10,000 or more** leave an account and return to it through **2 to 4 time-ordered transfers within 7 days**, with each hop carrying at least 80% of the previous hop.
5.3 Severity is MEDIUM for a 2-hop round trip and HIGH for 3 or more hops.
5.4 The analyst must document the business purpose of each intermediary account, or its absence.

## 6. High-risk geographies
6.1 High-risk jurisdictions are those on the FATF "Call for Action" list (black list) and "Jurisdictions under Increased Monitoring" (grey list), refreshed quarterly.
6.2 **Rule GEO_RISK:** raise an alert on any wire to or from a FATF black-list jurisdiction (CRITICAL), or wires to grey-list jurisdictions totalling **over $50,000 in 30 days** (HIGH).
6.3 Any counterparty that matches a sanctions list (OFAC SDN or equivalent) must be escalated immediately and the transaction blocked.

## 7. Customer risk rating
7.1 Each customer has a risk rating of LOW, MEDIUM or HIGH set at onboarding (Customer Due Diligence) and reviewed annually.
7.2 HIGH-risk customers (PEPs, cash-intensive businesses, high-risk geography nexus) require Enhanced Due Diligence; their alerts are raised one severity level.
7.3 Activity inconsistent with declared income or business profile (inflows over 3× declared monthly income for 2 consecutive months) is itself grounds for an alert.

## 8. Investigation and SAR filing
8.1 Every alert must receive a disposition within **30 days** of creation.
8.2 A SAR must be filed with FinCEN within **30 calendar days** of initial detection of facts that may constitute a basis for filing (31 CFR 1020.320). If no suspect is identified, the deadline may extend to 60 days.
8.3 A SAR narrative must state **who, what, when, where and why**: the subjects, the instruments and amounts, the dates, the accounts and locations, and why the activity is suspicious. It must reference the specific transactions.
8.4 Filing a SAR must not be disclosed to the subject (31 U.S.C. § 5318(g)(2)).

## 9. Use of automated tools
9.1 Detection rules are deterministic and documented in this policy. An AI assistant may summarise evidence and draft narratives, but **may not decide whether activity is suspicious**. That decision is always recorded by a named analyst or compliance officer.
9.2 Every AI-generated statement in a SAR draft must cite the transaction IDs or policy clauses it relies on.

## 10. Record keeping
10.1 Alerts, dispositions, SARs and supporting documentation are retained for **5 years** from the date of filing.

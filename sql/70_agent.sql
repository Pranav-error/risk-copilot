-- Cortex Agent: one natural-language entry point over the data (Cortex Analyst on the
-- semantic view) and the policy / FinCEN corpus (Cortex Search). Used by the Streamlit app.
-- No COPY GRANTS here (agents don't accept it): 80_governance.sql re-grants USAGE, so the two
-- must run in that order — as must 40 (search service) and 80.

CREATE OR REPLACE AGENT AML_COPILOT
  COMMENT = 'AML risk copilot: answers over transactions, alerts and decisions, citing policy'
  PROFILE = '{"display_name": "AML Risk Copilot"}'
  FROM SPECIFICATION
  $$
  models:
    orchestration: claude-sonnet-4-5

  orchestration:
    budget:
      seconds: 60
      tokens: 16000

  instructions:
    orchestration: >
      Use AmlData for any question about customers, accounts, transactions, amounts, alerts,
      severities or analyst decisions. Use AmlPolicy for what the bank's AML policy, FinCEN or
      FATF guidance says. For "why is this suspicious" questions use both: the numbers from
      AmlData and the clause from AmlPolicy.
    response: >
      You assist AML analysts; you never decide whether activity is suspicious or fraudulent,
      and you never state that anyone violated or committed anything. Report facts and say what
      they appear consistent with. Cite every number with the transaction, alert or account
      IDs it comes from, and every rule with its source document and section. Only call a
      policy clause breached when its threshold is met, and show the arithmetic. To explain an
      alert's severity, fetch SEVERITY_REASON from AmlData and repeat it; never infer a reason
      from the policy text (a severity uplift is not evidence of linked accounts). All amounts
      are USD. Be concise: a short answer, then a small table if there are several rows.
    sample_questions:
      - question: "How many open alerts are there by rule and severity?"
      - question: "Which high-risk customers deposited the most cash?"
      - question: "What does our policy say about structuring?"
      - question: "Why was alert 37 raised, and which policy clause does it match?"

  tools:
    - tool_spec:
        type: "cortex_analyst_text_to_sql"
        name: "AmlData"
        description: "Customers, accounts, money movement, alerts and analyst decisions for the bank"
    - tool_spec:
        type: "cortex_search"
        name: "AmlPolicy"
        description: "The bank's internal AML monitoring policy and FinCEN SAR guidance"

  tool_resources:
    AmlData:
      semantic_view: "RISK_COPILOT.AML.AML_SEMANTIC_VIEW"
      execution_environment:
        type: "warehouse"
        warehouse: "COMPUTE_WH"
    AmlPolicy:
      search_service: "RISK_COPILOT.AML.AML_POLICY_SEARCH"
      max_results: 4
      title_column: "SOURCE"
      id_column: "SECTION"
  $$;

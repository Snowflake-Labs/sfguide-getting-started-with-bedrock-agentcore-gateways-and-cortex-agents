#!/usr/bin/env python3
import json
import os
import re
from pathlib import Path

import pandas as pd
import requests
import streamlit as st


# ----------------------------
# Helpers: load/save settings
# ----------------------------
SETTINGS_PATH = Path("settings.json")

def load_settings():
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except Exception:
            return {}
    return {}

def save_settings(gateway_url, access_token):
    data = {"gateway_url": gateway_url, "access_token": access_token}
    SETTINGS_PATH.write_text(json.dumps(data, indent=2))


# ----------------------------
# Parser → structured object
# ----------------------------
def parse_stream_to_struct(resp_json):
    """
    Return a structured dict:
      {
        "answer": str,
        "sql": [str, ...],
        "tables": [{"columns": [...], "rows": [[...], ...]}, ...],
        "raw": resp_json
      }
    With sensible fallbacks (derived answer from first row, or search snippets).
    """
    result = {"answer": "", "sql": [], "tables": [], "raw": resp_json}
    try:
        items = (resp_json or {}).get("result", {}).get("content", [])
        text_blob = next((c.get("text", "") for c in items if c.get("type") == "text"), "")
        if not text_blob:
            return result

        env = json.loads(text_blob)
        payload = env.get("response", {}).get("payload", {}).get("content", "")
        if not payload:
            return result

        answer_chunks, sql_list, tables, search_snippets = [], [], [], []

        for line in payload.splitlines():
            if not line.startswith("data: ") or line.endswith("[DONE]"):
                continue
            try:
                ev = json.loads(line[6:])
            except Exception:
                continue

            for itm in ev.get("delta", {}).get("content", []):
                typ = itm.get("type")

                if typ == "output_text":
                    txt = itm.get("text", "")
                    if txt:
                        answer_chunks.append(txt.strip())

                elif typ == "tool_results":
                    tr = itm.get("tool_results", {})
                    for c in tr.get("content", []):
                        ctype = c.get("type")

                        if ctype == "json":
                            j = c.get("json", {}) or {}
                            # Analyst SQL
                            if "sql" in j:
                                sql_list.append(j["sql"])

                            # sql_exec table in json
                            cols = j.get("columns") or j.get("headers")
                            rows = j.get("rows") or j.get("records")
                            if rows:
                                tables.append({"columns": cols or [], "rows": rows})

                            # cortex_search snippets as fallback
                            if isinstance(j.get("searchResults"), list):
                                for r in j["searchResults"][:3]:
                                    snippet = r.get("text") or r.get("snippet") or r.get("chunk_text")
                                    if snippet:
                                        search_snippets.append(str(snippet).strip())
                            if "text" in j and isinstance(j["text"], str):
                                search_snippets.append(j["text"].strip())

                        elif ctype == "table":
                            tbl = c.get("table", {}) or {}
                            cols = tbl.get("headers") or tbl.get("columns") or []
                            rows = tbl.get("rows") or []
                            if rows:
                                tables.append({"columns": cols, "rows": rows})

        # Final answer with fallbacks
        answer = " ".join(answer_chunks).strip()

        if not answer and tables:
            cols = tables[0].get("columns") or []
            row = (tables[0].get("rows") or [None])[0]
            if row is not None:
                if isinstance(row, dict):
                    cols = list(row.keys())
                    row_vals = list(row.values())
                else:
                    row_vals = list(row)
                pairs = []
                for i, v in enumerate(row_vals):
                    label = cols[i] if i < len(cols) else f"col{i+1}"
                    pairs.append(f"{label}={v}")
                answer = "Top result: " + ", ".join(pairs)

        if not answer and search_snippets:
            answer = " • ".join(search_snippets[:2])

        if not answer and sql_list and not tables:
            answer = "Generated SQL but no rows were returned."

        result["answer"] = answer or "(no answer text)"
        result["sql"] = sql_list
        result["tables"] = tables
        return result

    except Exception:
        return result


# ----------------------------
# Bedrock Gateway call
# ----------------------------
def call_cortex_agent(gateway_url, access_token, tool_name, account_url, query, model="claude-4-sonnet"):
    formatted_account_url = account_url.split("://")[-1].rstrip("/")

    system_prompt = (
        "You are a helpful data analyst. "
        "Be greedy try to always use both semantic_view and cortex_search when needed."
        "for questions on 'opinions' make sure to use cortex_search, always! 'Opinions' will always mean to retrieve cortex_search text results"
        "opinions doesn't mean numerical ratings"
    )

    arguments = {
        "account_url": formatted_account_url,
        "model": model,
        "messages": [
            {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
            {"role": "user", "content": [{"type": "text", "text": query}]},
        ],
        "tool_choice": "auto",
        "tools": [
            {"tool_spec": {"type": "cortex_analyst_text_to_sql", "name": "data_model"}},
            {"tool_spec": {"type": "sql_exec", "name": "sql_exec"}},
            {"tool_spec": {"type": "cortex_search", "name": "cortex_search"}},
        ],
        "tool_resources": {
                        "cortex_search": {
                "name": "MOVIES.PUBLIC.MOVIE_SEARCH",
                "max_results": 10
            },
            "data_model": {
                # Change these to your semantic view
                "semantic_view": "MOVIES.PUBLIC.MOVIES_SEMANTIC_VIEW"
            },
            "sql_exec": {
                # Ensure this role/wh/db/schema can execute queries
                "role": "CORTEX_AGENT_ROLE",
                "warehouse": "WORKSHOP_WH",
                "database": "MOVIES",
                "schema": "PUBLIC"
            }
        }
    }

    payload = {
        "jsonrpc": "2.0",
        "id": "streamlit",
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments}
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}"
    }
    return requests.post(gateway_url, headers=headers, json=payload)


# ----------------------------
# Streamlit UI
# ----------------------------
st.set_page_config(page_title="Cortex Agents Demo", page_icon="🎬", layout="wide")
st.title("🎬 Cortex Agents Demo")

# Sidebar: Gateway settings
st.sidebar.header("Gateway settings")
settings = load_settings()
gateway_url = st.sidebar.text_input("Gateway URL", value=settings.get("gateway_url", ""), placeholder="https://<id>.gateway.bedrock-agentcore.../mcp")
access_token = st.sidebar.text_input("Access Token", value=settings.get("access_token", ""), type="password")
tool_name = st.sidebar.text_input("Tool Name", value="SnowflakeCortexTarget___runCortexAgent")
model = st.sidebar.text_input("Model", value="claude-4-sonnet")

if st.sidebar.button("Save settings"):
    save_settings(gateway_url, access_token)
    st.sidebar.success("Saved to settings.json")

# Main inputs
account_url = st.text_input("Snowflake account URL (e.g., myacct.snowflakecomputing.com)")
question = st.text_area("Your question", height=100, placeholder="e.g., What are the top romantic movies?")

run_btn = st.button("Ask")

if run_btn:
    if not (gateway_url and access_token and tool_name and account_url and question):
        st.error("Please fill in all fields (Gateway URL, Access Token, Tool Name, Account URL, and question).")
    else:
        with st.spinner("Calling Cortex Agent..."):
            resp = call_cortex_agent(gateway_url, access_token, tool_name, account_url, question, model=model)
        if resp.status_code != 200:
            st.error(f"Request failed: {resp.status_code}")
            st.code(resp.text, language="json")
        else:
            data = resp.json()
            parsed = parse_stream_to_struct(data)

            # Clean answer
            st.subheader("Answer")
            st.write(parsed["answer"] or "(no answer)")

            # Generated SQL (if any)
            if parsed["sql"]:
                st.subheader("Generated SQL")
                for i, sql in enumerate(parsed["sql"], 1):
                    st.caption(f"SQL #{i}")
                    st.code(sql, language="sql")

            # Result table (first table, if present)
            if parsed["tables"]: 
                st.subheader("Results")
                first = parsed["tables"][0]
                cols = first.get("columns") or []
                rows = first.get("rows") or []
                # Normalize to DataFrame
                if rows and isinstance(rows[0], dict):
                    df = pd.DataFrame(rows)
                else:
                    df = pd.DataFrame(rows, columns=cols if cols else None)
                st.dataframe(df, use_container_width=True)

            # Raw payload (collapsible)
            with st.expander("Raw streaming payload"):
                st.code(json.dumps(data, indent=2), language="json")
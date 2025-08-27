#!/usr/bin/env python3
# app.py

import json
from pathlib import Path
import re
import unicodedata as _ud

import pandas as pd
import requests
import streamlit as st

# =========================================================
# Settings helpers
# =========================================================
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

# =========================================================
# Text cleanup (mojibake + unicode normalization)
# =========================================================
def _fix_mojibake(s: str) -> str:
    """Repair common UTF-8→latin1/cp1252 mojibake."""
    if not isinstance(s, str):
        return s
    cands = [s]
    for enc in ("latin1", "cp1252"):
        try:
            cands.append(s.encode(enc, "strict").decode("utf-8", "strict"))
        except Exception:
            pass

    def _score(t):
        ctrl = sum(ord(ch) < 32 and ch not in "\n\t\r" for ch in t)
        repl = t.count("�")
        high = sum(ord(ch) > 127 for ch in t)
        return -(ctrl * 3 + repl * 5 + high)

    return max(cands, key=_score)

def _clean_text(s: str) -> str:
    """Normalize, drop control/zero-widths, trim weird bracket artifacts."""
    if not isinstance(s, str):
        return s
    s = _ud.normalize("NFKC", s)

    # remove zero-width & bidi controls
    zw = {
        "\u200b", "\u200c", "\u200d", "\ufeff", "\u2060",
        "\u202a", "\u202b", "\u202c", "\u202d", "\u202e"
    }
    s = "".join(
        ch for ch in s
        if (ch in "\n\t\r" or (_ud.category(ch)[0] != "C" and ch not in zw))
    )

    # drop lone CJK bracket markers like 【1】, 〔2〕, 〖3〗
    s = re.sub(
        r"[\u3010\u3014\u3016\u301a\u3018\u301e]\s*\d+\s*[\u3011\u3015\u3017\u301b\u3019\u301f]",
        "",
        s,                      # <— third argument was missing before
    )

    # collapse spaces/newlines
    s = re.sub(r"[ \t\u00a0]+", " ", s)
    s = re.sub(r"\s+\n", "\n", s)
    return s.strip()

# =========================================================
# Parser → structured object
# =========================================================
def parse_stream_to_struct(resp_json):
    """
    Parse the Bedrock Gateway SSE envelope embedded in result.content[0].text.

    Returns:
      {
        "answer": str,
        "sql": [str],
        "tables": [{"columns": [...], "rows": [...]}],
        "search_snippets": [str],
        "raw": resp_json
      }
    """
    out = {"answer": "", "sql": [], "tables": [], "search_snippets": [], "raw": resp_json}

    # 1) Extract the escaped SSE blob
    try:
        items = (resp_json or {}).get("result", {}).get("content", [])
        text_blob = next((c.get("text", "") for c in items if c.get("type") == "text"), "")
    except Exception:
        text_blob = ""

    if not isinstance(text_blob, str) or not text_blob.strip():
        out["answer"] = "(no answer text)"
        return out

    # 2) Unwrap outer JSON -> payload string
    try:
        env = json.loads(text_blob)  # {"response":{"payload":{"content":"event:...\n"}}}
        payload = env.get("response", {}).get("payload", {}).get("content", "") or ""
    except Exception:
        payload = ""

    if not isinstance(payload, str) or not payload:
        out["answer"] = "(no answer text)"
        return out

    # 3) Decode escapes (\\n, \\" and \uXXXX) → then repair mojibake + clean
    try:
        payload = payload.encode("utf-8").decode("unicode_escape")
    except Exception:
        pass
    payload = _fix_mojibake(payload)
    # also normalize newlines that might remain double-escaped
    payload = payload.replace("\\r\\n", "\n").replace("\\n", "\n")
    payload = _clean_text(payload)

    # 4) Walk every SSE "data: {...}" record
    answer_chunks = []
    sql_list = []
    tables = []
    search_snips = []

    for m in re.finditer(r"(?m)^data:\s*(\{.*\})\s*$", payload):
        data_str = m.group(1).strip()
        if data_str in ("[DONE]", "DONE"):
            continue
        try:
            ev = json.loads(data_str)
        except Exception:
            continue

        delta = (ev or {}).get("delta", {}) or {}
        for itm in delta.get("content", []) or []:
            typ = itm.get("type")

            # Assistant streamed text
            if typ in ("text", "output_text"):
                t = itm.get("text", "")
                if isinstance(t, str) and t.strip():
                    answer_chunks.append(t)

            # Tool result structures
            elif typ == "tool_results":
                tr = itm.get("tool_results", {}) or {}
                for c in tr.get("content", []) or []:
                    ctype = c.get("type")

                    if ctype == "text":
                        t = c.get("text", "")
                        if isinstance(t, str) and t.strip():
                            search_snips.append(t.strip())

                    elif ctype == "json":
                        j = c.get("json", {}) or {}

                        # Analyst/sql_exec SQL
                        if isinstance(j.get("sql"), str) and j["sql"].strip():
                            sql_list.append(j["sql"])

                        # tabular shapes
                        cols = j.get("columns") or j.get("headers")
                        rows = j.get("rows") or j.get("records")
                        if rows:
                            tables.append({"columns": cols or [], "rows": rows})

                        # cortex_search results
                        if isinstance(j.get("searchResults"), list):
                            for r in j["searchResults"]:
                                if isinstance(r, dict):
                                    for k in ("text", "snippet", "chunk_text", "matched_text"):
                                        v = r.get(k)
                                        if isinstance(v, str) and v.strip():
                                            search_snips.append(v.strip())

                        # flat text sometimes present
                        if isinstance(j.get("text"), str) and j["text"].strip():
                            search_snips.append(j["text"].strip())

                    elif ctype == "table":
                        tbl = c.get("table", {}) or {}
                        cols = tbl.get("headers") or tbl.get("columns") or []
                        rows = tbl.get("rows") or []
                        if rows:
                            tables.append({"columns": cols, "rows": rows})

    # 5) Build final answer with fallbacks
    answer = " ".join(answer_chunks).strip()

    if not answer and tables:
        cols = tables[0].get("columns") or []
        first_row = (tables[0].get("rows") or [None])[0]
        if first_row is not None:
            if isinstance(first_row, dict):
                cols = list(first_row.keys())
                vals = [first_row[k] for k in cols]
            else:
                vals = list(first_row)
            pairs = []
            for i, v in enumerate(vals):
                label = cols[i] if i < len(cols) else f"col{i+1}"
                pairs.append(f"{label}={v}")
            answer = "Top result: " + ", ".join(pairs)

    if not answer and search_snips:
        answer = " ".join(search_snips[:2])

    # Final cleanup
    answer = _clean_text(_fix_mojibake(answer))
    search_snips = [_clean_text(_fix_mojibake(s)) for s in search_snips if isinstance(s, str) and s.strip()]

    out["answer"] = answer or "(no answer text)"
    out["sql"] = sql_list
    out["tables"] = tables
    out["search_snippets"] = search_snips
    return out

# =========================================================
# Bedrock Gateway call
# =========================================================
def call_cortex_agent(gateway_url, access_token, tool_name, account_url, query, model="claude-4-sonnet"):
    formatted_account_url = account_url.split("://")[-1].rstrip("/")

    system_prompt = (
        "You are a helpful data analyst. "
        "For quantitative questions, use the semantic view; "
        "for unstructured content, use Cortex Search. Always respond with a concise answer."
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
            "data_model": {"semantic_view": "MOVIES.PUBLIC.MOVIES_SEMANTIC_VIEW"},
            "cortex_search": {"name": "MOVIES.PUBLIC.MOVIE_SEARCH", "max_results": 3},
            "sql_exec": {
                "role": "CORTEX_AGENT_ROLE",
                "warehouse": "WORKSHOP_WH",
                "database": "MOVIES",
                "schema": "PUBLIC",
            },
        },
    }

    payload = {
        "jsonrpc": "2.0",
        "id": "streamlit",
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
    }
    return requests.post(gateway_url, headers=headers, json=payload)

# =========================================================
# Streamlit UI
# =========================================================
st.set_page_config(page_title="Cortex Agents Demo", page_icon="🎬", layout="wide")
st.title("🎬 Cortex Agents Demo")

# Sidebar: Gateway settings
st.sidebar.header("Gateway settings")
settings = load_settings()
gateway_url = st.sidebar.text_input(
    "Gateway URL",
    value=settings.get("gateway_url", ""),
    placeholder="https://<id>.<region>.amazonaws.com/mcp",
)
access_token = st.sidebar.text_input(
    "Access Token",
    value=settings.get("access_token", ""),
    type="password",
)
tool_name = st.sidebar.text_input("Tool Name", value="SnowflakeCortexTarget___runCortexAgent")
model = st.sidebar.text_input("Model", value="claude-4-sonnet")

if st.sidebar.button("Save settings"):
    save_settings(gateway_url, access_token)
    st.sidebar.success("Saved to settings.json")

# Main inputs
account_url = st.text_input("Snowflake account URL (e.g., myacct.snowflakecomputing.com)")
question = st.text_area(
    "Your question",
    height=100,
    placeholder='e.g., What are the unstructured reviews for the movie "Toy Story"?',
)

if st.button("Ask"):
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

            # Search snippets (if any)
            if parsed["search_snippets"]:
                st.subheader("Search snippets")
                for s in parsed["search_snippets"]:
                    st.markdown(f"- {s}")

            # Raw payload (collapsible)
            with st.expander("Raw streaming payload"):
                st.code(json.dumps(data, indent=2), language="json")

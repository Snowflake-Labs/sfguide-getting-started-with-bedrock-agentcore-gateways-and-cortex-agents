#!/usr/bin/env python3
# multi_target_app.py - Enhanced Streamlit app with Wikipedia integration

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd
import requests
import streamlit as st

# =========================================
# Settings helpers (same as original)
# =========================================
SETTINGS_PATH = Path("settings.json")

def _defaults() -> dict:
    return {
        "gateway_url": "",
        "access_token": "",
        "sql_api": {
            "token": "",
            "auth_mode": "Bearer (OAuth/PAT)",
            "warehouse": "WORKSHOP_WH",
            "database": "MOVIES",
            "schema": "PUBLIC",
            "role": "CORTEX_AGENT_ROLE",
        },
        "targets": {
            "cortex": {"tools": ["SnowflakeCortexTarget___runAgent"]},
            "wikipedia": {"tools": ["WikipediaTarget___getPageSummary", "WikipediaTarget___getPageMedia"]}
        }
    }

def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        try:
            data = json.loads(SETTINGS_PATH.read_text())
            base = _defaults()
            base.update({k: data.get(k, base[k]) for k in base})
            if isinstance(data.get("sql_api"), dict):
                base["sql_api"].update(data["sql_api"])
            if isinstance(data.get("targets"), dict):
                base["targets"].update(data["targets"])
            return base
        except Exception:
            return _defaults()
    return _defaults()

def save_settings(settings: dict) -> None:
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2))

# =========================================
# Multi-target Gateway calls
# =========================================
def call_cortex_agent(
    gateway_url: str,
    access_token: str,
    tool_name: str,
    account_url: str,
    query: str,
    model: str,
    database: str,
    schema: str,
    agent_name: str,
):
    """Call Cortex agent (same as original)"""
    formatted_account_url = account_url.split("://")[-1].rstrip("/")

    system_prompt = (
        "You are a helpful data analyst. "
        "For quantitative questions, use the semantic view; "
        "for unstructured content, use Cortex Search. Always produce a concise final answer."
    )

    new_arguments = {
        "account_url": formatted_account_url,
        "database": database,
        "schema": schema,
        "agent": agent_name,
        "model": model,
        "Accept": "application/json",
        "messages": [
            {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
            {"role": "user", "content": [{"type": "text", "text": query}]},
        ],
    }

    headers = {"Content-Type": "application/json", "Accept": "application/json", "Authorization": f"Bearer {access_token}"}
    payload = {"jsonrpc": "2.0", "id": "streamlit", "method": "tools/call", "params": {"name": tool_name, "arguments": new_arguments}}
    
    return requests.post(gateway_url, headers=headers, json=payload)

def call_wikipedia_api(
    gateway_url: str,
    access_token: str,
    tool_name: str,
    title: str,
):
    """Call Wikipedia API through the gateway"""
    # Format title for Wikipedia (replace spaces with underscores, handle special cases)
    formatted_title = title.replace(" ", "_")
    
    # Handle common movie title patterns
    if "toy story" in title.lower():
        formatted_title = "Toy_Story"
    elif "avatar" in title.lower() and "2009" not in title.lower():
        formatted_title = "Avatar_(2009_film)"
    elif "titanic" in title.lower() and "1997" not in title.lower():
        formatted_title = "Titanic_(1997_film)"
    elif "sudden death" in title.lower():
        formatted_title = "Sudden_Death_(1995_film)"
    elif "grumpier old men" in title.lower():
        formatted_title = "Grumpier_Old_Men"
    elif "heat" in title.lower() and ("1995" in title.lower() or len(title.strip()) <= 6):
        formatted_title = "Heat_(1995_film)"
    
    headers = {"Content-Type": "application/json", "Accept": "application/json", "Authorization": f"Bearer {access_token}"}
    payload = {
        "jsonrpc": "2.0", 
        "id": "streamlit-wiki", 
        "method": "tools/call", 
        "params": {
            "name": tool_name, 
            "arguments": {"title": formatted_title}
        }
    }
    
    return requests.post(gateway_url, headers=headers, json=payload)

def normalize_payload(s: str) -> str:
    """Repeatedly unescape until 'data:' lines are readable."""
    if not isinstance(s, str):
        return ""
    r = s
    for _ in range(5):
        if "data:" in r and "\n" in r:
            break
        try:
            r = json.loads(f'"{r}"')  # JSON unescape
            continue
        except Exception:
            pass
        try:
            r = bytes(r, "utf-8").decode("unicode_escape")  # unicode escape
        except Exception:
            break
    r = r.replace("\\r\\n", "\n").replace("\\n", "\n")
    return r

def parse_sse_content(sse_text: str) -> str:
    """Parse Server-Sent Events content to extract clean text using the proven parsing logic"""
    if not sse_text or 'event:' not in sse_text:
        return sse_text
    
    # Normalize the payload first
    payload = normalize_payload(sse_text)
    
    answer_chunks: List[str] = []
    final_answer_text: str = ""
    
    current_event = ""
    for line in payload.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
            continue
        if not line.startswith("data:"):
            continue
        data_str = line[5:].strip()
        if data_str in ("[DONE]", "DONE", ""):
            continue

        ev = None
        for _ in range(2):
            try:
                ev = json.loads(data_str)
                break
            except Exception:
                try:
                    data_str = (
                        data_str.encode("utf-8").decode("unicode_escape").replace('\\"', '"')
                    )
                except Exception:
                    break
        if not isinstance(ev, dict):
            continue

        # Prefer the final assembled response
        if current_event == "response" and ev.get("role") and isinstance(ev.get("content"), list):
            for item in ev.get("content"):
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    final_answer_text = item["text"].strip() or final_answer_text
            continue

        # Streamed assistant text
        if current_event == "response.text.delta" and isinstance(ev.get("text"), str):
            t = ev.get("text") or ""
            if t.strip():
                answer_chunks.append(t.rstrip())
            continue

        # Ignore thinking deltas explicitly
        if current_event == "response.thinking.delta":
            continue

    # Return the final answer or joined chunks
    answer = (final_answer_text.strip() if final_answer_text else "\n".join(answer_chunks).strip())
    return answer if answer else sse_text

def extract_movie_titles_from_query(query: str) -> List[str]:
    """Extract potential movie titles from the user query"""
    # Simple extraction - look for quoted strings or common movie patterns
    quoted_matches = re.findall(r'"([^"]+)"', query)
    if quoted_matches:
        return quoted_matches
    
    # Look for common movie title patterns after certain keywords
    movie_keywords = ['toy story', 'avatar', 'titanic', 'avengers', 'star wars', 'harry potter', 'lord of the rings', 'sudden death', 'grumpier old men', 'heat']
    found_movies = []
    query_lower = query.lower()
    
    for movie in movie_keywords:
        if movie in query_lower:
            found_movies.append(movie.title())
    
    return found_movies

def extract_movie_titles_from_cortex_response(cortex_data: dict) -> List[str]:
    """Extract movie titles from Cortex response data"""
    movie_titles = []
    
    try:
        # Look for movie titles in the result data
        result = cortex_data.get('result', {})
        content = result.get('content', [])
        
        for item in content:
            if item.get('type') == 'tool_result':
                tool_result = item.get('content', [])
                for tool_item in tool_result:
                    if tool_item.get('type') == 'json':
                        json_data = tool_item.get('json', {})
                        result_set = json_data.get('result_set', {})
                        data = result_set.get('data', [])
                        
                        # Extract movie titles from the data rows
                        for row in data[:3]:  # Top 3 movies
                            if row and len(row) > 0:
                                movie_title = str(row[0]).strip()
                                if movie_title and movie_title not in movie_titles:
                                    movie_titles.append(movie_title)
    except Exception as e:
        print(f"Error extracting movie titles: {e}")
    
    return movie_titles

def parse_cortex_response_properly(cortex_data: dict) -> str:
    """Parse Cortex response to extract clean, readable text"""
    try:
        result = cortex_data.get('result', {})
        content = result.get('content', [])
        
        for item in content:
            if item.get('type') == 'text':
                text_content = item.get('text', '')
                
                # Check if it's SSE content that needs parsing
                if 'event:' in text_content and 'data:' in text_content:
                    # Parse the SSE content
                    parsed_text = parse_sse_content(text_content)
                    if parsed_text and parsed_text != text_content:
                        return parsed_text
                else:
                    # Already clean text
                    return text_content
        
        # Fallback: return JSON representation
        return "Could not parse Cortex response properly. See detailed response below."
        
    except Exception as e:
        return f"Error parsing Cortex response: {e}"

def format_combined_response(cortex_response: dict, wikipedia_responses: List[dict], movie_titles: List[str]) -> str:
    """Combine Cortex and Wikipedia responses into a comprehensive answer"""
    combined_text = []
    
    # Add Cortex response first
    if cortex_response and 'result' in cortex_response:
        combined_text.append("## 📊 Movie Data Analysis")
        
        # Use the improved parsing function
        clean_text = parse_cortex_response_properly(cortex_response)
        combined_text.append(clean_text)
    
    # Add Wikipedia summaries
    if wikipedia_responses and movie_titles:
        combined_text.append("\n## 📚 Wikipedia Information")
        
        for i, (title, wiki_resp) in enumerate(zip(movie_titles, wikipedia_responses)):
            if wiki_resp and 'result' in wiki_resp:
                wiki_result = wiki_resp.get('result', {})
                if 'extract' in wiki_result:
                    combined_text.append(f"\n### {title}")
                    combined_text.append(wiki_result['extract'])
                elif 'content' in wiki_result:
                    # Handle different response formats
                    content = wiki_result['content']
                    if isinstance(content, list) and content:
                        text_item = next((item for item in content if item.get('type') == 'text'), None)
                        if text_item and 'text' in text_item:
                            combined_text.append(f"\n### {title}")
                            combined_text.append(text_item['text'])
    
    return "\n".join(combined_text) if combined_text else "No information found."

# =========================================
# Streamlit UI
# =========================================
st.set_page_config(page_title="Multi-Target Gateway Demo", page_icon="🎬🔍", layout="wide")
st.title("🎬🔍 Multi-Target Gateway Demo")
st.caption("Combining Snowflake Cortex data analysis with Wikipedia knowledge")

settings = load_settings()

# Sidebar settings
st.sidebar.header("Gateway Settings")
gateway_url = st.sidebar.text_input("Gateway URL", value=settings.get("gateway_url", ""), placeholder="https://<id>.<region>.amazonaws.com/mcp")
access_token = st.sidebar.text_input("Access Token", value=settings.get("access_token", ""), type="password")

# Tool selection
st.sidebar.subheader("Available Tools")
cortex_tools = settings.get("targets", {}).get("cortex", {}).get("tools", ["SnowflakeCortexTarget___runAgent"])
wikipedia_tools = settings.get("targets", {}).get("wikipedia", {}).get("tools", ["WikipediaTarget___getPageSummary"])

cortex_tool = st.sidebar.selectbox("Cortex Tool", cortex_tools, index=0)
wikipedia_tool = st.sidebar.selectbox("Wikipedia Tool", wikipedia_tools, index=0)

model = st.sidebar.text_input("Model", value="claude-4-sonnet")

st.sidebar.markdown("---")
st.sidebar.subheader("Snowflake SQL API")
env_token = os.getenv("SNOWFLAKE_SQL_API_TOKEN") or os.getenv("SNOWFLAKE_OAUTH_TOKEN")
saved_token = (settings.get("sql_api") or {}).get("token") or ""
resolved_token = env_token or saved_token
remember_token = st.sidebar.checkbox("Remember token in settings.json", value=True)
sf_api_token = st.sidebar.text_input("SQL API token", value=resolved_token, type="password")
auth_mode = st.sidebar.selectbox("Auth header", ["Bearer (OAuth/PAT)", "Snowflake Token"], index=0)
sf_warehouse = st.sidebar.text_input("Warehouse", value=settings.get("sql_api", {}).get("warehouse", "WORKSHOP_WH"))
sf_database = st.sidebar.text_input("Database", value=settings.get("sql_api", {}).get("database", "MOVIES"))
sf_schema = st.sidebar.text_input("Schema", value=settings.get("sql_api", {}).get("schema", "PUBLIC"))
sf_role = st.sidebar.text_input("Role", value=settings.get("sql_api", {}).get("role", "CORTEX_AGENT_ROLE"))

if st.sidebar.button("Save Settings"):
    new_settings = {
        "gateway_url": gateway_url,
        "access_token": access_token,
        "sql_api": {
            "token": sf_api_token if remember_token else "",
            "auth_mode": auth_mode,
            "warehouse": sf_warehouse,
            "database": sf_database,
            "schema": sf_schema,
            "role": sf_role,
        },
    }
    save_settings(new_settings)
    st.sidebar.success("Settings saved!")

# Main content
col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("Query Configuration")
    account_url = st.text_input("Snowflake account URL", placeholder="myacct.snowflakecomputing.com")
    db_input = st.text_input("Database (for Agent)", value=sf_database)
    schema_input = st.text_input("Schema (for Agent)", value=sf_schema)
    agent_input = st.text_input("Agent name", value="MOVIESAGENT")

with col2:
    st.subheader("Multi-Target Features")
    use_wikipedia = st.checkbox("Include Wikipedia summaries", value=True, help="Automatically fetch Wikipedia info for detected movie titles")
    auto_detect_movies = st.checkbox("Auto-detect movie titles", value=True, help="Automatically extract movie titles from your query")

# Query input
st.subheader("Your Question")
# Initialize session state for question if not exists
if 'question' not in st.session_state:
    st.session_state.question = ""

question = st.text_area(
    "Ask about movies - the system will query both Cortex and Wikipedia:",
    value=st.session_state.question,
    height=100,
    placeholder="e.g., What are the ratings for Toy Story? Tell me about the movie too.",
    help="Try asking about specific movies - the system will automatically fetch Wikipedia summaries!"
)

# Update session state when text area changes
if question != st.session_state.question:
    st.session_state.question = question

# Example queries
st.caption("💡 **Try these example queries:**")
example_cols = st.columns(3)
with example_cols[0]:
    if st.button("🎬 Toy Story Analysis", help="Get ratings + Wikipedia info"):
        st.session_state.question = "What are the ratings for Toy Story? Also tell me about the movie Toy Story."

with example_cols[1]:
    if st.button("📈 Top Movies Summary", help="Get top movies + their Wikipedia pages"):
        st.session_state.question = "What are the top-rated movies and give me Wikipedia summaries for the top 3?"

with example_cols[2]:
    if st.button("🔍 Movie Comparison", help="Compare movies with background info"):
        st.session_state.question = "Compare the ratings of Sudden Death and Grumpier Old Men, and provide Wikipedia background on both."

# Main action
if st.button("🚀 Ask Multi-Target Gateway"):
    if not all([gateway_url, access_token, cortex_tool, account_url, db_input, schema_input, agent_input, question]):
        st.error("Please fill in all required fields.")
    else:
        # Extract movie titles if auto-detection is enabled
        movie_titles = []
        if use_wikipedia and auto_detect_movies:
            movie_titles = extract_movie_titles_from_query(question)
            if movie_titles:
                st.info(f"🎬 Detected movies: {', '.join(movie_titles)}")
        
        # For "top movies" queries, we'll extract titles from the Cortex response
        extract_from_response = "top" in question.lower() and ("movie" in question.lower() or "film" in question.lower())

        # Call Cortex
        with st.spinner("🎯 Querying Cortex Agent..."):
            cortex_response = call_cortex_agent(
                gateway_url, access_token, cortex_tool, account_url, question,
                model, db_input, schema_input, agent_input
            )

        # Call Wikipedia for detected movies
        wikipedia_responses = []
        if use_wikipedia and movie_titles:
            with st.spinner("📚 Fetching Wikipedia summaries..."):
                for title in movie_titles:
                    try:
                        wiki_resp = call_wikipedia_api(gateway_url, access_token, wikipedia_tool, title)
                        wikipedia_responses.append(wiki_resp.json() if wiki_resp.status_code == 200 else None)
                    except Exception as e:
                        st.warning(f"Could not fetch Wikipedia data for {title}: {e}")
                        wikipedia_responses.append(None)

        # Display results
        if cortex_response.status_code != 200:
            st.error(f"Cortex request failed: {cortex_response.status_code}")
            st.code(cortex_response.text, language="json")
        else:
            cortex_data = cortex_response.json()
            
            # Extract movie titles from Cortex response if needed
            if extract_from_response and use_wikipedia:
                extracted_titles = extract_movie_titles_from_cortex_response(cortex_data)
                if extracted_titles and not movie_titles:
                    movie_titles = extracted_titles[:3]  # Top 3 movies
                    st.info(f"🎬 Extracted top movies: {', '.join(movie_titles)}")
                    
                    # Now call Wikipedia for these movies
                    with st.spinner("📚 Fetching Wikipedia summaries for top movies..."):
                        for title in movie_titles:
                            try:
                                wiki_resp = call_wikipedia_api(gateway_url, access_token, wikipedia_tool, title)
                                wikipedia_responses.append(wiki_resp.json() if wiki_resp.status_code == 200 else None)
                            except Exception as e:
                                st.warning(f"Could not fetch Wikipedia data for {title}: {e}")
                                wikipedia_responses.append(None)
            
            # Parse and display the response properly
            if use_wikipedia and (movie_titles or wikipedia_responses):
                st.subheader("🎯 Combined Analysis")
                combined_response = format_combined_response(cortex_data, wikipedia_responses, movie_titles)
                st.markdown(combined_response)
                
                # Show individual responses in expanders
                with st.expander("📊 Detailed Cortex Response"):
                    st.json(cortex_data)
                
                if wikipedia_responses:
                    with st.expander("📚 Wikipedia API Responses"):
                        for i, (title, resp) in enumerate(zip(movie_titles, wikipedia_responses)):
                            if resp:
                                st.subheader(f"{title}")
                                st.json(resp)
            else:
                # Show just Cortex response with proper parsing
                st.subheader("📊 Movie Data Analysis")
                parsed_cortex = parse_cortex_response_properly(cortex_data)
                st.markdown(parsed_cortex)

# Footer with instructions
st.markdown("---")
st.markdown("""
### 🔧 How it works:
1. **Cortex Target**: Queries your Snowflake data for movie ratings, analytics, etc.
2. **Wikipedia Target**: Fetches movie summaries, cast info, and background details
3. **Smart Combination**: Automatically detects movie titles and enriches responses

### 💡 Tips:
- Use specific movie titles in quotes for better detection: `"Toy Story"`
- Ask comparative questions to get rich, multi-source answers  
- Enable auto-detection to automatically fetch Wikipedia context
- Try the example buttons: Toy Story analysis, Top Movies summary, or Sudden Death vs Grumpier Old Men comparison
""")

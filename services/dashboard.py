"""Streamlit dashboard: chat UI over the /chat backend + routing transparency."""
from __future__ import annotations

import os

import clickhouse_connect
import httpx
import pandas as pd
import streamlit as st

from commit_pulse.config import get_settings

st.set_page_config(page_title="commit-pulse", page_icon="📈", layout="wide")

CHAT_API_URL = os.getenv("CHAT_API_URL", "http://localhost:8002").rstrip("/")
ROUTE_BADGES = {
    "relational": "🗄️ relational → Neon Postgres",
    "analytical": "📊 analytical → ClickHouse",
    "semantic": "🔍 semantic → Chroma",
    "chained": "🔗 chained → Chroma → Postgres",
}


@st.cache_resource
def _settings():
    return get_settings()


def ask_backend(question: str) -> dict:
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(f"{CHAT_API_URL}/chat", json={"question": question})
        resp.raise_for_status()
        return resp.json()


def commits_per_day_chart():
    """Optional chart: commits per day straight from ClickHouse."""
    try:
        s = _settings()
        client = clickhouse_connect.get_client(
            host=s.clickhouse_host, port=s.clickhouse_port,
            username=s.clickhouse_user, password=s.clickhouse_password,
            database=s.clickhouse_db,
        )
        result = client.query(
            """
            SELECT toDate(committed_at) AS day, repo, count() AS commits
            FROM commit_metrics
            GROUP BY day, repo
            ORDER BY day
            """
        )
        if not result.result_rows:
            st.info("Belum ada data untuk chart.")
            return
        df = pd.DataFrame(result.result_rows, columns=result.column_names)
        df["day"] = pd.to_datetime(df["day"])
        st.bar_chart(df, x="day", y="commits", color="repo")
    except Exception as exc:
        st.warning(f"Chart tidak tersedia: {exc}")


st.title("commit-pulse — tanya riwayat commit")
st.caption("AI chat router di atas Kafka → Postgres / ClickHouse / Chroma (demo breadth-of-demonstration)")

with st.sidebar:
    st.header("Routing decision")
    if last := st.session_state.get("last_route"):
        st.markdown(f"**{ROUTE_BADGES.get(last['route'], last['route'])}**")
        st.write("intent:", f"`{last['params'].get('intent')}`")
        st.write("reasoning:", last.get("reasoning") or "—")
        with st.expander("params"):
            st.json({k: v for k, v in last["params"].items() if v is not None})
    else:
        st.write("Belum ada pertanyaan.")

    st.divider()
    st.header("Commits per day (ClickHouse)")
    commits_per_day_chart()

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if question := st.chat_input("contoh: who changed README? / commits per day / commits about bug fixes"):
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        try:
            with st.spinner("routing + querying…"):
                result = ask_backend(question)
            st.markdown(f"*{ROUTE_BADGES.get(result['route'], result['route'])}*")
            st.markdown(result["answer"])
            st.session_state.messages.append({"role": "assistant", "content": result["answer"]})
            st.session_state.last_route = result
            st.rerun()
        except httpx.HTTPError as exc:
            st.error(f"Chat backend tidak bisa dihubungi di {CHAT_API_URL}: {exc}")

"""Streamlit ops dashboard: volume, decisions, cost, rule-layer coverage."""

import os

import pandas as pd
import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000")

st.set_page_config(page_title="Doc Automation Dashboard", layout="wide")
st.title("📄 Logistics Document Automation")


@st.cache_data(ttl=15)
def fetch_documents() -> pd.DataFrame:
    items, page = [], 1
    while True:
        r = requests.get(f"{API_URL}/documents", params={"page": page}, timeout=10)
        r.raise_for_status()
        data = r.json()
        items += data["items"]
        if page * data["page_size"] >= data["total"]:
            break
        page += 1
    return pd.DataFrame(items)


try:
    df = fetch_documents()
except requests.RequestException as e:
    st.error(f"API not reachable at {API_URL}: {e}")
    st.stop()

if df.empty:
    st.info("No documents processed yet. POST files to /extract or trigger the n8n workflow.")
    st.stop()

total = len(df)
auto = (df["decision"] == "auto_approve").sum()
review = (df["decision"] == "human_review").sum()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Documents processed", total)
c2.metric("Auto-approved", f"{auto} ({auto/total:.0%})")
c3.metric("Human intervention rate", f"{(total-auto)/total:.0%}")
c4.metric("Total LLM cost", f"${df['cost_usd'].sum():.4f}")

left, right = st.columns(2)
with left:
    st.subheader("Decisions")
    st.bar_chart(df["decision"].value_counts())
with right:
    st.subheader("Cost per document (USD)")
    st.bar_chart(df.set_index("filename")["cost_usd"])

st.subheader("Latency")
st.write(
    f"p50: **{df['latency_ms'].median():.0f} ms** · "
    f"p95: **{df['latency_ms'].quantile(0.95):.0f} ms**"
)

st.subheader("Documents")
status_filter = st.multiselect("Status", sorted(df["status"].unique()), default=list(df["status"].unique()))
st.dataframe(
    df[df["status"].isin(status_filter)][
        ["filename", "doc_type", "status", "decision", "overall_confidence",
         "tokens_used", "cost_usd", "latency_ms", "created_at"]
    ],
    use_container_width=True,
)

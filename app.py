"""
app.py - Streamlit UI for the fraud investigation agent.

Run (PowerShell, GEMINI_API_KEY must be set in this window):
    streamlit run app.py
"""

import asyncio
import json
import os
import re

import streamlit as st
import streamlit.components.v1 as components

import graph_tools as gt
import memory_tools as mt
from agent_gemini import DEFAULT_QUESTION, investigate

st.set_page_config(page_title="Graph Fraud Squad", page_icon="🕸️", layout="wide")

st.markdown(
    """
    <style>
    :root {
        --ink: #e8edf5;
        --muted: #9aa8ba;
        --panel: #111b29;
        --line: rgba(148, 163, 184, 0.18);
        --accent: #62d6c8;
    }

    [data-testid="stAppViewContainer"] {
        background:
            radial-gradient(ellipse at 12% 0%, rgba(50, 111, 143, 0.18), transparent 38%),
            #0b1118;
    }

    [data-testid="stHeader"] {
        background: transparent;
    }

    [data-testid="stMain"] > div {
        padding-top: 1.8rem;
    }

    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #111b29 0%, #0d141e 100%);
        border-right: 1px solid var(--line);
    }

    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
    [data-testid="stSidebar"] label {
        color: var(--ink);
    }

    .hero {
        padding: 1.6rem 1.8rem;
        margin-bottom: 1.4rem;
        border: 1px solid var(--line);
        border-radius: 18px;
        background: linear-gradient(115deg, rgba(20, 35, 50, 0.96), rgba(15, 26, 39, 0.88));
        box-shadow: 0 18px 50px rgba(0, 0, 0, 0.18);
    }

    .hero-kicker {
        margin: 0 0 0.55rem;
        color: var(--accent);
        font-size: 0.72rem;
        font-weight: 700;
        letter-spacing: 0.16em;
        text-transform: uppercase;
    }

    .hero h1 {
        margin: 0;
        color: var(--ink);
        font-size: clamp(1.8rem, 3vw, 2.5rem);
        letter-spacing: -0.04em;
        line-height: 1.12;
    }

    .hero p:last-child {
        max-width: 760px;
        margin: 0.7rem 0 0;
        color: var(--muted);
        font-size: 1rem;
        line-height: 1.6;
    }

    [data-testid="stTabs"] [role="tablist"] {
        gap: 0.4rem;
        border-bottom: 1px solid var(--line);
    }

    [data-testid="stTabs"] button[role="tab"] {
        min-height: 3rem;
        border-radius: 10px 10px 0 0;
        color: var(--muted);
        font-weight: 600;
    }

    [data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
        color: var(--accent);
        background: rgba(98, 214, 200, 0.08);
    }

    [data-testid="stTextArea"] textarea {
        border: 1px solid var(--line);
        border-radius: 12px;
        background: rgba(17, 27, 41, 0.8);
        color: var(--ink);
        line-height: 1.55;
    }

    [data-testid="stTextArea"] textarea:focus {
        border-color: var(--accent);
        box-shadow: 0 0 0 1px var(--accent);
    }

    [data-testid="stButton"] button {
        border-radius: 10px;
        font-weight: 650;
        transition: transform 120ms ease, box-shadow 120ms ease;
    }

    [data-testid="stButton"] button[kind="primary"] {
        border: 1px solid rgba(98, 214, 200, 0.45);
        background: linear-gradient(135deg, #1a8f86, #176f82);
        color: #f5fffe;
        box-shadow: 0 8px 22px rgba(23, 111, 130, 0.22);
    }

    [data-testid="stButton"] button:hover {
        transform: translateY(-1px);
    }

    [data-testid="stMetric"] {
        padding: 1rem;
        border: 1px solid var(--line);
        border-radius: 14px;
        background: rgba(17, 27, 41, 0.72);
    }

    [data-testid="stExpander"] {
        border-color: var(--line);
        border-radius: 12px;
        background: rgba(17, 27, 41, 0.42);
    }

    [data-testid="stAlert"] {
        border-radius: 12px;
    }

    @media (max-width: 640px) {
        [data-testid="stMain"] > div {
            padding: 1rem 0.8rem 2rem;
        }

        .hero {
            padding: 1.2rem;
            border-radius: 14px;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ------------------------------------------------------------- helpers
def pretty(text):
    try:
        return json.loads(text)
    except Exception:
        return text


def collect_ids(answer, trace):
    """Pull account and merchant ids out of everything the agent saw and said."""
    blob = answer + " " + " ".join(
        json.dumps(t["input"]) + " " + t["output"] for t in trace
    )
    accounts = set(re.findall(r"\b(?:R\d+_\d+|BRIDGE|A\d+)\b", blob))
    merchants = set(re.findall(r"\bMS?\d+\b", blob))
    return sorted(accounts)[:60], sorted(merchants)[:20]


def build_graph_html(account_ids, merchant_ids):
    from pyvis.network import Network

    net = Network(height="560px", width="100%", bgcolor="#0e1117",
                  font_color="white", cdn_resources="in_line")
    net.barnes_hut(spring_length=140)

    def add(node_id, **kw):
        if node_id not in net.get_nodes():
            net.add_node(node_id, **kw)

    used = gt._g.query(
        """
        MATCH (a:Account)-[:USED]->(d:Device)
        WHERE a.id IN $ids
        RETURN a.id, a.frozen, a.age_days, d.id
        """,
        {"ids": account_ids},
    ).result_set
    for acc, frozen, age, dev in used:
        add(acc, label=acc, shape="dot", size=18,
            color="#e5484d" if frozen else "#f5a524",
            title=f"{acc}\nage: {age} days\nfrozen: {bool(frozen)}")
        add(dev, label=dev, shape="square", size=12, color="#3e63dd",
            title=f"device {dev}")
        net.add_edge(acc, dev, color="#6b7280")

    paid = gt._g.query(
        """
        MATCH (a:Account)-[:MADE]->(t:Transaction)-[:TO]->(m:Merchant)
        WHERE a.id IN $ids AND m.id IN $mids
        RETURN a.id, m.id, m.name, sum(t.amount)
        """,
        {"ids": account_ids, "mids": merchant_ids},
    ).result_set
    for acc, mid, mname, total in paid:
        add(mid, label=mid, shape="diamond", size=20, color="#8e4ec6",
            title=f"{mname}\nmerchant {mid}")
        add(acc, label=acc, shape="dot", size=18, color="#f5a524")
        net.add_edge(acc, mid, color="#8e4ec6", title=f"paid {round(total, 2)}")

    return net.generate_html()


# ------------------------------------------------------------- sidebar
st.sidebar.title("🕸️ Graph Fraud Squad")
analyst = st.sidebar.text_input("Analyst (own memory graph)", value="alice").strip() or "alice"
os.environ["ANALYST"] = analyst  # used by memory panel in this process
safe_name = re.sub(r"\W", "", analyst) or "alice"
st.sidebar.caption(f"Memory graph: `memory_{safe_name}`")

if st.sidebar.button("♻️ Reset demo (unfreeze all accounts)"):
    gt._run("MATCH (a:Account) REMOVE a.frozen, a.freeze_reason")
    st.session_state.pop("result", None)
    st.sidebar.success("Accounts unfrozen.")

# ------------------------------------------------------------- main
st.markdown(
    """
    <section class="hero">
        <p class="hero-kicker">Graph Fraud Squad · Investigation Desk</p>
        <h1>Uncover fraud through connected data.</h1>
        <p>Investigate suspicious activity across connected accounts, devices, and merchants.
        Review the evidence, graph links, and analyst memory in one place.</p>
    </section>
    """,
    unsafe_allow_html=True,
)

question = st.text_area("Ask the investigator", value=DEFAULT_QUESTION, height=80)

if st.button("🔍 Investigate", type="primary"):
    live = st.status("Investigating...", expanded=True)

    def on_step(step):
        live.write(f"🔧 **{step['tool']}** `{json.dumps(step['input'])}`")

    try:
        answer, trace = asyncio.run(investigate(question, analyst=analyst, on_step=on_step))
        live.update(label=f"Done: {len(trace)} tool calls", state="complete")
        st.session_state["result"] = {"answer": answer, "trace": trace, "analyst": analyst}
    except Exception as e:
        live.update(label="Failed", state="error")
        st.error(f"{type(e).__name__}: {e}")

result = st.session_state.get("result")

tab_report, tab_trace, tab_graph, tab_mem = st.tabs(
    ["📝 Report", "🧭 Reasoning trace", "🕸️ Graph", "🧠 Memory & actions"]
)

with tab_report:
    if result:
        st.markdown(result["answer"])
    else:
        st.info("Run an investigation to see the report.")

with tab_trace:
    if result:
        st.caption("Every tool call, with the Cypher query behind it.")
        for i, step in enumerate(result["trace"], 1):
            out = pretty(step["output"])
            with st.expander(f"{i}. {step['tool']}  {json.dumps(step['input'])}"):
                if isinstance(out, dict) and "cypher" in out:
                    st.markdown("**Cypher**")
                    st.code(out["cypher"], language="cypher")
                    st.markdown("**Result**")
                    st.json(out.get("data"))
                else:
                    st.json(out)
    else:
        st.info("Nothing yet.")

with tab_graph:
    if result:
        acc_ids, mer_ids = collect_ids(result["answer"], result["trace"])
        st.caption("🟠 account  🔴 frozen account  🟦 device  🟣 merchant")
        try:
            components.html(build_graph_html(acc_ids, mer_ids), height=580, scrolling=False)
        except Exception as e:
            st.error(f"Could not draw graph: {e}")
    else:
        st.info("Nothing yet.")

with tab_mem:
    col1, col2 = st.columns(2)
    with col1:
        st.subheader(f"Memory of `{analyst}`")
        lessons = mt.get_lessons()["data"]["lessons"]
        st.markdown("**Lessons learned**")
        if lessons:
            for l in lessons:
                st.write(f"• {l['rule']}  _(used {l['times_used']}x)_")
        else:
            st.caption("none yet")

        st.markdown("**Past cases**")
        try:
            g, _ = mt._mem()
            cases = g.query(
                "MATCH (c:Case) RETURN c.id, c.verdict, c.summary "
                "ORDER BY c.created DESC LIMIT 5"
            ).result_set
        except Exception:
            cases = []
        if cases:
            for cid, verdict, summary in cases:
                with st.expander(f"{cid}: {verdict}"):
                    st.write(summary)
        else:
            st.caption("none yet")
    with col2:
        st.subheader("Actions taken")
        frozen = gt.get_frozen_accounts()["data"]
        st.metric("Frozen accounts", len(frozen))
        if frozen:
            st.dataframe(frozen, use_container_width=True)

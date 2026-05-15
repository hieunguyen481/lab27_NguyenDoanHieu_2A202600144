"""Exercise 5 - Streamlit approval UI for the HITL PR review agent.

Run with:
    uv run streamlit run app.py
"""

from __future__ import annotations

import asyncio
import uuid

import streamlit as st
from dotenv import load_dotenv
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from common.db import db_conn, db_path
from exercises.exercise_4_audit import build_graph


load_dotenv()


if "thread_id" not in st.session_state:
    st.session_state.thread_id = None
if "pr_url" not in st.session_state:
    st.session_state.pr_url = ""
if "interrupt_payload" not in st.session_state:
    st.session_state.interrupt_payload = None
if "final" not in st.session_state:
    st.session_state.final = None


st.set_page_config(page_title="HITL PR Review", layout="wide")
st.title("HITL PR Review Agent")


async def load_recent_sessions() -> list[dict]:
    async with db_conn() as conn:
        async with conn.execute(
            """
            SELECT thread_id,
                   pr_url,
                   MIN(timestamp) AS started,
                   MAX(timestamp) AS last_event,
                   CASE
                     WHEN SUM(CASE WHEN risk_level = 'high' THEN 1 ELSE 0 END) > 0 THEN 'high'
                     WHEN SUM(CASE WHEN risk_level = 'med' THEN 1 ELSE 0 END) > 0 THEN 'med'
                     ELSE 'low'
                   END AS worst_risk,
                   COUNT(*) AS events
              FROM audit_events
             GROUP BY thread_id, pr_url
             ORDER BY MAX(timestamp) DESC
             LIMIT 10
            """
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


with st.sidebar:
    st.header("Recent sessions")
    try:
        sessions = asyncio.run(load_recent_sessions())
    except Exception as exc:
        sessions = []
        st.caption(f"No sessions yet ({exc})")

    if not sessions:
        st.caption("No audit sessions yet.")

    for row in sessions:
        st.caption(f"{row['worst_risk'].upper()} - {row['events']} events")
        if st.button(row["pr_url"], key=f"session_{row['thread_id']}"):
            st.session_state.thread_id = row["thread_id"]
            st.session_state.pr_url = row["pr_url"]
            st.session_state.interrupt_payload = None
            st.session_state.final = None
            st.rerun()
        st.caption(f"`{row['thread_id']}`")


with st.form("start"):
    pr_url = st.text_input(
        "PR URL",
        value=st.session_state.pr_url,
        placeholder="https://github.com/VinUni-AI20k/PR-Demo/pull/1",
    )
    submitted = st.form_submit_button("Run review")


def render_approval_card(payload: dict) -> dict | None:
    """58-72% bucket: show the LLM review and return a resume dict on click."""
    conf = payload["confidence"]
    st.subheader(f"Approval requested - confidence {conf:.0%}")
    st.caption(payload["confidence_reasoning"])
    st.markdown(payload["summary"])

    for comment in payload.get("comments", []):
        st.markdown(
            f"- **[{comment['severity']}]** "
            f"`{comment['file']}:{comment.get('line') or '?'}` - {comment['body']}"
        )

    with st.expander("Diff"):
        st.code(payload.get("diff_preview", ""), language="diff")

    feedback = st.text_input("Feedback (optional)", key="approval_feedback")
    col1, col2, col3 = st.columns(3)
    if col1.button("Approve", type="primary"):
        return {"choice": "approve", "feedback": feedback}
    if col2.button("Reject"):
        return {"choice": "reject", "feedback": feedback}
    if col3.button("Edit"):
        return {"choice": "edit", "feedback": feedback}
    return None


def render_escalation_card(payload: dict) -> dict | None:
    """< 58% bucket: show risk factors and collect reviewer answers."""
    conf = payload["confidence"]
    st.subheader(f"Strong escalation - confidence {conf:.0%}")
    st.caption(payload["confidence_reasoning"])
    if payload.get("risk_factors"):
        st.error("Risks: " + ", ".join(payload["risk_factors"]))
    st.markdown(payload["summary"])

    with st.form("escalation"):
        answers = {
            question: st.text_input(question, key=f"escalation_{idx}")
            for idx, question in enumerate(payload["questions"])
        }
        submitted_answers = st.form_submit_button("Submit answers", type="primary")
        if submitted_answers:
            if not any(answer.strip() for answer in answers.values()):
                st.warning("Please answer at least one question.")
            else:
                return answers
    return None


async def run_graph(pr_url: str, thread_id: str, resume_value=None):
    """Invoke the graph once. Returns final state or an interrupt result."""
    async with AsyncSqliteSaver.from_conn_string(db_path()) as cp:
        await cp.setup()
        app = build_graph(cp)
        cfg = {"configurable": {"thread_id": thread_id}}
        if resume_value is None:
            return await app.ainvoke({"pr_url": pr_url, "thread_id": thread_id}, cfg)
        return await app.ainvoke(Command(resume=resume_value), cfg)


if submitted and pr_url:
    st.session_state.pr_url = pr_url
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.interrupt_payload = None
    st.session_state.final = None

    with st.spinner("Fetching PR and asking the LLM..."):
        try:
            result = asyncio.run(run_graph(pr_url, st.session_state.thread_id))
        except Exception as exc:
            st.error(f"Review failed: {exc}")
            result = None

    if result and "__interrupt__" in result:
        st.session_state.interrupt_payload = result["__interrupt__"][0].value
    elif result:
        st.session_state.final = result


payload = st.session_state.interrupt_payload
if payload is not None:
    kind = payload["kind"]
    if kind == "approval_request":
        answer = render_approval_card(payload)
    elif kind == "escalation":
        answer = render_escalation_card(payload)
    else:
        st.error(f"Unknown interrupt kind: {kind}")
        answer = None

    if answer is not None:
        with st.spinner("Resuming..."):
            try:
                result = asyncio.run(
                    run_graph(
                        st.session_state.pr_url,
                        st.session_state.thread_id,
                        resume_value=answer,
                    )
                )
            except Exception as exc:
                st.error(f"Resume failed: {exc}")
                result = None

        if result and "__interrupt__" in result:
            st.session_state.interrupt_payload = result["__interrupt__"][0].value
        elif result:
            st.session_state.interrupt_payload = None
            st.session_state.final = result
            st.rerun()


if st.session_state.final is not None:
    final = st.session_state.final
    action = final.get("final_action", "?")
    if action.startswith("auto") or action.startswith("committed"):
        st.success(f"{action} - comment posted to {st.session_state.pr_url}")
    elif action == "rejected":
        st.warning("Rejected - no comment posted")
    else:
        st.info(f"final_action = {action}")
    st.caption(
        f"thread_id = {st.session_state.thread_id} - replay: "
        f"`.venv\\Scripts\\python.exe -m audit.replay --thread {st.session_state.thread_id}`"
    )

"""
Upwork Job Researcher + Proposal Generator
Skip the Noise Media — Reddit Certified Partner

Launch: python3 upwork_tool.py
"""

import streamlit as st
from datetime import datetime, timezone
from upwork_api import (
    KEYWORD_GROUPS,
    CLIENT_ID,
    CLIENT_SECRET,
    STORED_ACCESS_TOKEN,
    has_client_credentials,
    get_auth_url,
    exchange_code_for_token,
    search_jobs,
    fetch_job_questions,
    get_last_api_error,
)
from proposal_generator import generate_proposal

st.set_page_config(
    page_title="Upwork Job Finder — Skip the Noise",
    page_icon="🎯",
    layout="wide",
)

# ── Session state init ─────────────────────────────────────────────────────────
if "access_token" not in st.session_state:
    st.session_state.access_token = STORED_ACCESS_TOKEN
if "jobs" not in st.session_state:
    st.session_state.jobs = []
if "proposals" not in st.session_state:
    st.session_state.proposals = {}
if "searched" not in st.session_state:
    st.session_state.searched = False
if "dismissed" not in st.session_state:
    st.session_state.dismissed = set()


def _save_token_to_env(token):
    """Write UPWORK_ACCESS_TOKEN into .env so it persists across restarts."""
    from pathlib import Path
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    lines = env_path.read_text().splitlines()
    new_lines = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("UPWORK_ACCESS_TOKEN=") or stripped.startswith("# UPWORK_ACCESS_TOKEN="):
            new_lines.append(f"UPWORK_ACCESS_TOKEN={token}")
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        new_lines.append(f"UPWORK_ACCESS_TOKEN={token}")
    env_path.write_text("\n".join(new_lines) + "\n")


# ── Auto-capture OAuth callback (?code= in URL) ────────────────────────────────
_oauth_code = st.query_params.get("code")
if _oauth_code and not st.session_state.access_token:
    with st.spinner("Completing OAuth connection..."):
        _result = exchange_code_for_token(_oauth_code)
    if _result and "access_token" in _result:
        _token = _result["access_token"]
        st.session_state.access_token = _token
        st.query_params.clear()
        _save_token_to_env(_token)
        st.rerun()
    else:
        st.error(f"OAuth callback failed: {get_last_api_error()}")
        st.query_params.clear()


def score_badge(score):
    if score >= 8:
        return f"🟢 {score}/10"
    elif score >= 5:
        return f"🟡 {score}/10"
    else:
        return f"🔴 {score}/10"


def format_client(client):
    parts = []
    if client.get("paymentVerificationStatus") == "VERIFIED":
        parts.append("✅ Payment verified")
    rating = float(client.get("totalFeedback") or 0)
    if rating:
        parts.append(f"⭐ {rating:.1f}")
    jobs_posted = int(client.get("totalPostedJobs") or 0)
    if jobs_posted:
        parts.append(f"{jobs_posted} jobs posted")
    total_spent = (client.get("totalSpent") or {}).get("amount")
    if total_spent:
        try:
            spent_k = float(total_spent) / 1000
            if spent_k >= 1:
                parts.append(f"${spent_k:.0f}k spent")
        except Exception:
            pass
    return " · ".join(parts) if parts else "No client history"


def format_time_ago(created):
    if not created:
        return ""
    try:
        dt = datetime.fromisoformat(created.replace("Z", "+00:00").replace(" ", "T"))
        hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        if hours < 1:
            return "just now"
        if hours < 24:
            return f"{int(hours)}h ago"
        return f"{int(hours / 24)}d ago"
    except Exception:
        return ""


def _job_hours_old(job):
    """Return hours since job was posted (for recency filtering/sorting)."""
    created = job.get("created", "")
    if not created:
        return 9999
    try:
        dt = datetime.fromisoformat(created.replace("Z", "+00:00").replace(" ", "T"))
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except Exception:
        return 9999


def _job_budget_value(job):
    """Parse budget string into a comparable number for sorting."""
    budget = str(job.get("budget", ""))
    import re
    # Extract the first number found (handles "$500", "$15/hr", "$1,000-$5,000")
    nums = re.findall(r"[\d,]+", budget)
    if nums:
        try:
            return float(nums[0].replace(",", ""))
        except Exception:
            pass
    return 0


# ── Header ─────────────────────────────────────────────────────────────────────
st.title("🎯 Upwork Job Finder")
st.caption("Skip the Noise Media — Reddit Certified Partner")

# ── Auth check ────────────────────────────────────────────────────────────────
is_authed = bool(st.session_state.access_token)

if not is_authed:
    with st.container(border=True):
        st.subheader("Connect Your Upwork Account")

        if not has_client_credentials():
            st.error("Add `UPWORK_CLIENT_ID` and `UPWORK_CLIENT_SECRET` to your `.env` file first.")
            st.stop()

        auth_url = get_auth_url()
        st.markdown(f"**[Click here to authorize on Upwork]({auth_url})**")
        st.info("After approving, Upwork will redirect you back here automatically and connect.")
    st.stop()

# ── Sidebar: Search Controls ───────────────────────────────────────────────────
with st.sidebar:
    st.header("Search Jobs")

    # Service areas with Select All / None
    st.subheader("Service Areas")
    col_all, col_none = st.columns(2)
    with col_all:
        if st.button("All", use_container_width=True, key="select_all"):
            for g in KEYWORD_GROUPS:
                st.session_state[f"kw_{g}"] = True
    with col_none:
        if st.button("None", use_container_width=True, key="select_none"):
            for g in KEYWORD_GROUPS:
                st.session_state[f"kw_{g}"] = False

    selected_groups = []
    for group_name in KEYWORD_GROUPS:
        default = group_name in ["Reddit Ads", "Meta / Facebook Ads", "Campaign Management"]
        key = f"kw_{group_name}"
        if key not in st.session_state:
            st.session_state[key] = default
        if st.checkbox(group_name, value=st.session_state[key], key=key):
            selected_groups.append(group_name)

    custom_kw = st.text_input(
        "Custom keywords (comma-separated)",
        placeholder="e.g. affiliate marketing, influencer",
    )

    st.subheader("Filters")
    job_type = st.radio("Job Type", ["All", "Fixed-Price", "Hourly"], index=0)
    job_type_param = {"All": "all", "Fixed-Price": "fixed", "Hourly": "hourly"}[job_type]

    posted_within = st.selectbox(
        "Posted within",
        ["All time", "Last 24h", "Last 48h", "Last 7 days"],
        index=0,
    )
    posted_hours = {"All time": None, "Last 24h": 24, "Last 48h": 48, "Last 7 days": 168}[posted_within]

    min_score = st.slider("Minimum Relevance Score", 0, 10, 4)

    sort_by = st.selectbox(
        "Sort by",
        ["Score (high to low)", "Budget (high to low)", "Newest first"],
        index=0,
    )

    search_clicked = st.button("🔍 Search Jobs", type="primary", use_container_width=True)

    if st.session_state.searched and st.session_state.jobs:
        st.divider()
        visible = sum(
            1 for j in st.session_state.jobs
            if j["score"] >= min_score
            and (posted_hours is None or _job_hours_old(j) <= posted_hours)
        )
        st.caption(f"{len(st.session_state.jobs)} found · {visible} shown")

    st.divider()
    if st.button("Disconnect", use_container_width=True):
        st.session_state.access_token = ""
        st.session_state.jobs = []
        st.session_state.proposals = {}
        st.session_state.searched = False
        st.rerun()

# ── Search Logic ───────────────────────────────────────────────────────────────
if search_clicked:
    keywords = []
    for group in selected_groups:
        keywords.extend(KEYWORD_GROUPS[group])
    if custom_kw:
        keywords.extend([k.strip() for k in custom_kw.split(",") if k.strip()])

    if not keywords:
        st.warning("Select at least one service area or enter custom keywords.")
    else:
        with st.spinner(f"Searching {len(keywords)} keyword(s)..."):
            jobs = search_jobs(
                keywords,
                job_type=job_type_param,
                limit=50,
                token=st.session_state.access_token,
            )
        err = get_last_api_error()
        if err and not jobs:
            st.error(f"API Error: {err}")
            if "401" in str(err) or "403" in str(err):
                st.info("Token may be expired. Click Disconnect in the sidebar and reconnect.")
        else:
            st.session_state.jobs = jobs
            st.session_state.searched = True
            if err:
                st.warning(f"Some searches failed: {err}")

# ── Results ────────────────────────────────────────────────────────────────────
if st.session_state.searched:
    # Filter
    jobs = [
        j for j in st.session_state.jobs
        if j["score"] >= min_score
        and j["id"] not in st.session_state.dismissed
        and (posted_hours is None or _job_hours_old(j) <= posted_hours)
    ]

    # Sort
    if sort_by == "Score (high to low)":
        jobs = sorted(jobs, key=lambda j: j["score"], reverse=True)
    elif sort_by == "Budget (high to low)":
        jobs = sorted(jobs, key=_job_budget_value, reverse=True)
    elif sort_by == "Newest first":
        jobs = sorted(jobs, key=_job_hours_old)

    total_found = len(st.session_state.jobs)
    dismissed_count = len(st.session_state.dismissed)

    if not jobs:
        st.info("No jobs matched your filters. Try lowering the score, broadening the recency window, or adding more keywords.")
        if dismissed_count:
            if st.button("Restore dismissed jobs"):
                st.session_state.dismissed = set()
                st.rerun()
    else:
        st.caption(
            f"Showing {len(jobs)} of {total_found} jobs · sorted by {sort_by.lower()}"
            + (f" · {dismissed_count} dismissed" if dismissed_count else "")
        )

        for job in jobs:
            jid = job["id"]
            with st.container(border=True):
                # ── Title row ──────────────────────────────────────────────────
                col1, col2, col3, col4 = st.columns([5, 2, 1, 1])
                with col1:
                    title_md = f"**{job['title']}**"
                    if job.get("url"):
                        title_md = f"**[{job['title']}]({job['url']})**"
                    st.markdown(title_md)
                with col2:
                    st.markdown(f"💰 `{job['budget']}`")
                with col3:
                    st.markdown(score_badge(job["score"]))
                with col4:
                    if st.button("✕", key=f"dismiss_{jid}", help="Dismiss this job"):
                        st.session_state.dismissed.add(jid)
                        st.rerun()

                # ── Client + time row ──────────────────────────────────────────
                time_str = format_time_ago(job["created"])
                client_str = format_client(job["client"])
                caption_parts = [client_str]
                if time_str:
                    caption_parts.append(time_str)
                st.caption(" · ".join(caption_parts))

                # ── Skills ─────────────────────────────────────────────────────
                if job["skills"]:
                    st.markdown(" ".join(f"`{s}`" for s in job["skills"][:8]))

                # ── Description preview ────────────────────────────────────────
                desc = (job["description"] or "").replace("\n", " ").strip()
                preview = desc[:400] + ("..." if len(desc) > 400 else "")
                st.caption(preview)

                # ── Proposal controls ──────────────────────────────────────────
                col_gen, col_regen, col_spacer = st.columns([2, 2, 4])
                with col_gen:
                    if st.button("✍️ Generate Proposal", key=f"gen_{jid}"):
                        # Check for manually entered questions first
                        manual_raw = st.session_state.get(f"manual_q_{jid}", "")
                        manual_qs = [q.strip() for q in manual_raw.splitlines() if q.strip()]
                        # Fetch screening questions on-demand (not in search results)
                        with st.spinner("Checking for screening questions..."):
                            auto_qs, q_err = fetch_job_questions(
                                jid,
                                ciphertext=job.get("ciphertext"),
                                token=st.session_state.access_token,
                            )
                        # Manual questions take priority; fall back to auto-fetched
                        questions = manual_qs or auto_qs or []
                        job["questions"] = questions
                        job["questions_err"] = q_err if not manual_qs else None
                        spinner_msg = "Writing proposal & answers..." if questions else "Writing proposal..."
                        with st.spinner(spinner_msg):
                            proposal = generate_proposal(
                                title=job["title"],
                                description=job["description"],
                                budget=job["budget"],
                                skills=job["skills"],
                                client_info=format_client(job["client"]),
                                questions=questions or None,
                            )
                        st.session_state.proposals[jid] = proposal

                # ── Manual screening questions input ───────────────────────────
                with st.expander("📋 Add screening questions manually"):
                    manual_qs_raw = st.text_area(
                        "Questions",
                        placeholder="Paste each question on a new line, e.g.\nWhat's your experience with Reddit Ads?\nHow do you approach creative testing?",
                        height=100,
                        key=f"manual_q_{jid}",
                        label_visibility="collapsed",
                    )
                    if st.button("💬 Answer These Questions", key=f"answer_q_{jid}"):
                        qs_list = [q.strip() for q in manual_qs_raw.splitlines() if q.strip()]
                        if qs_list:
                            with st.spinner("Writing proposal & answers..."):
                                result = generate_proposal(
                                    title=job["title"],
                                    description=job["description"],
                                    budget=job["budget"],
                                    skills=job["skills"],
                                    client_info=format_client(job["client"]),
                                    questions=qs_list,
                                )
                            st.session_state.proposals[jid] = result
                            st.rerun()
                        else:
                            st.caption("Paste at least one question first.")

                if jid in st.session_state.proposals:
                    proposal_text = st.session_state.proposals[jid]

                    with col_regen:
                        if st.button("🔄 Regenerate", key=f"regen_{jid}"):
                            cached_questions = job.get("questions") or []
                            with st.spinner("Rewriting..."):
                                proposal_text = generate_proposal(
                                    title=job["title"],
                                    description=job["description"],
                                    budget=job["budget"],
                                    skills=job["skills"],
                                    client_info=format_client(job["client"]),
                                    questions=cached_questions or None,
                                )
                            st.session_state.proposals[jid] = proposal_text

                    # Show warning if question fetch failed (vs. job genuinely having none)
                    if job.get("questions_err"):
                        st.warning(f"⚠️ Couldn't auto-fetch screening questions. Paste them in the box above.")

                    if proposal_text.startswith("Error:"):
                        st.error(proposal_text)
                    else:
                        # Split proposal from Q&A answers (separated by ---)
                        if "\n---\n" in proposal_text:
                            proposal_part, qa_part = proposal_text.split("\n---\n", 1)
                        else:
                            proposal_part = proposal_text
                            qa_part = ""

                        # ── Proposal box ──────────────────────────────────────
                        word_count = len(proposal_part.split())
                        wc_label = f"📝 {word_count} words"
                        if word_count > 150:
                            wc_label += " ⚠️ over 150 — trim before sending"
                        st.caption(wc_label)
                        edited_proposal = st.text_area(
                            "Proposal",
                            value=proposal_part.strip(),
                            height=200,
                            key=f"proposal_text_{jid}",
                            label_visibility="collapsed",
                        )

                        # ── Screening Q&A box (shown only when present) ───────
                        if qa_part.strip():
                            st.caption("📋 Screening question answers — paste each into Upwork's question fields")
                            edited_qa = st.text_area(
                                "Screening answers",
                                value=qa_part.strip(),
                                height=150,
                                key=f"qa_text_{jid}",
                                label_visibility="collapsed",
                            )
                            # Persist edits to both parts
                            st.session_state.proposals[jid] = edited_proposal + "\n---\n" + edited_qa
                        else:
                            if edited_proposal != proposal_part.strip():
                                st.session_state.proposals[jid] = edited_proposal

elif not st.session_state.searched:
    st.markdown("""
### How to use

1. Select service areas in the sidebar (use **All / None** to toggle quickly)
2. Set filters: job type, posted within, minimum score
3. Hit **🔍 Search Jobs**
4. Click **✍️ Generate Proposal** on any job that looks good

Proposals are written in your voice. Edit directly in the text box before sending.
""")

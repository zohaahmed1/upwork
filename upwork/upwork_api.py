"""
Upwork API client — OAuth 2.0 Authorization Code flow + GraphQL job search.

Flow:
  1. get_auth_url()  → user opens in browser, approves
  2. exchange_code_for_token(code)  → get access_token
  3. search_jobs(...)  → signed Bearer requests to GraphQL

Agency: Skip the Noise Media
"""

import os
import requests
from pathlib import Path
from datetime import datetime, timezone

# ── Load .env (local dev) ──────────────────────────────────────────────────────
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# ── Load Streamlit secrets ─────────────────────────────────────────────────────
_st_secrets = {}
try:
    import streamlit as st
    if hasattr(st, "secrets"):
        for _key in ["UPWORK_CLIENT_ID", "UPWORK_CLIENT_SECRET", "UPWORK_ACCESS_TOKEN"]:
            try:
                if _key in st.secrets:
                    _st_secrets[_key] = st.secrets[_key]
            except Exception:
                pass
except Exception:
    pass


def _env(key, default=""):
    return _st_secrets.get(key) or os.environ.get(key, default)


# ── Credentials ───────────────────────────────────────────────────────────────
CLIENT_ID = _env("UPWORK_CLIENT_ID")
CLIENT_SECRET = _env("UPWORK_CLIENT_SECRET")
STORED_ACCESS_TOKEN = _env("UPWORK_ACCESS_TOKEN")  # cached token from previous OAuth flow

# ── Endpoints ─────────────────────────────────────────────────────────────────
GRAPHQL_URL = "https://api.upwork.com/graphql"
TOKEN_URL = "https://www.upwork.com/api/v3/oauth2/token"
AUTH_URL = "https://www.upwork.com/ab/account-security/oauth2/authorize"
REDIRECT_URI = "http://localhost:8502"

_HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.upwork.com",
    "Referer": "https://www.upwork.com/",
    "Content-Type": "application/json",
}

# ── Keyword groups — refined from 1225 proposal history (25.5% win rate) ──────
# Winning patterns: "management" 41%, "strategy/creative" 37-38%, "setup" 35%,
# "dtc" 35%, "campaign" 32%, "google" 31%, "reddit/meta/facebook" 28-30%
KEYWORD_GROUPS = {
    "Reddit Ads": ["reddit ads", "reddit advertising"],
    "Meta / Facebook Ads": ["meta ads", "facebook ads", "facebook advertising"],
    "Campaign Management": ["campaign management", "ads manager", "media buyer"],
    "Creative Strategist": ["creative strategist", "ad creative", "ugc creative"],
    "B2B SaaS Paid": ["b2b saas ads", "saas paid media", "b2b paid ads"],
    "Google + Meta": ["google meta ads", "google facebook ads", "ppc meta"],
    "DTC / eComm Ads": ["dtc ads", "ecommerce ads", "shopify ads"],
    "Performance Marketing": ["performance marketing", "paid media specialist"],
}

# Positive keyword scores — specific paid-ads signals only.
# Generic terms (saas, agency, instagram, cpa) removed: appear in too many
# unrelated jobs and inflate scores for non-fits.
_SCORE_KEYWORDS = {
    # Highest specificity — core services (4 pts)
    "reddit ads": 4,
    "reddit advertising": 4,
    # Strong paid-ads signals (3 pts)
    "meta ads": 3,
    "facebook ads": 3,
    "facebook advertising": 3,
    "campaign management": 3,
    "creative strategist": 3,
    "creative strategy": 3,
    "media buyer": 3,
    "paid social": 3,
    "performance marketing": 3,
    "paid media": 3,
    # Supporting paid-ads signals (2 pts)
    "ad creative": 2,
    "campaign setup": 2,
    "roas": 2,
    "google ads": 2,
    "tiktok ads": 2,
    "ugc ads": 2,
    "dtc ads": 2,
    "ecommerce ads": 2,
    "b2b saas ads": 2,
    "paid advertising": 2,
    # Contextual signals — only add value when other signals already present (1 pt)
    "dtc": 1,
    "ecommerce": 1,
    "shopify ads": 1,
    "b2b paid": 1,
}

# Negative signals — deduct for clear wrong-fits
_NEGATIVE_KEYWORDS = {
    "seo": -3,
    "search engine optimization": -3,
    "organic social": -2,
    "content writing": -2,
    "copywriting": -2,
    "web design": -2,
    "website development": -2,
    "wordpress": -2,
    "influencer marketing": -2,
    "email marketing": -1,
    "email campaign": -1,
    "graphic design": -1,
}

_last_api_error = None


def get_last_api_error():
    return _last_api_error


def has_client_credentials():
    return bool(CLIENT_ID and CLIENT_SECRET)


def get_auth_url():
    """Build the OAuth 2.0 authorization URL for the user to visit."""
    return (
        f"{AUTH_URL}"
        f"?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
    )


def exchange_code_for_token(code):
    """Exchange an authorization code for an access token.

    Returns dict with 'access_token' key on success, None on failure.
    """
    global _last_api_error
    try:
        resp = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code.strip(),
                "redirect_uri": REDIRECT_URI,
            },
            auth=(CLIENT_ID, CLIENT_SECRET),
            timeout=30,
        )
        resp.raise_for_status()
        _last_api_error = None
        return resp.json()
    except requests.HTTPError as e:
        _last_api_error = f"Token exchange failed ({e.response.status_code}): {e.response.text[:400]}"
        return None
    except Exception as e:
        _last_api_error = f"Token exchange error: {e}"
        return None


def _gql(query, variables=None, token=None):
    """Execute a GraphQL query. Returns data dict or None on error."""
    global _last_api_error
    tok = token or STORED_ACCESS_TOKEN
    if not tok:
        _last_api_error = "No access token. Complete OAuth setup first."
        return None
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    try:
        resp = requests.post(
            GRAPHQL_URL,
            headers={
                **_HEADERS_BASE,
                "Authorization": f"Bearer {tok}",
            },
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        if "errors" in result:
            _last_api_error = f"GraphQL error: {result['errors'][0].get('message', str(result['errors']))}"
            return None
        _last_api_error = None
        return result.get("data")
    except requests.HTTPError as e:
        _last_api_error = f"HTTP {e.response.status_code}: {e.response.text[:300]}"
        return None
    except Exception as e:
        _last_api_error = str(e)
        return None


_JOB_SEARCH_QUERY = """
query SearchJobs($searchExpr: String!) {
  marketplaceJobPostingsSearch(
    marketPlaceJobFilter: {
      searchExpression_eq: $searchExpr
      verifiedPaymentOnly_eq: true
    }
  ) {
    totalCount
    edges {
      node {
        id
        ciphertext
        title
        description
        createdDateTime
        engagement
        hourlyBudgetType
        amount { displayValue rawValue }
        hourlyBudgetMin { displayValue rawValue }
        hourlyBudgetMax { displayValue rawValue }
        skills { name }
        client {
          totalFeedback
          totalPostedJobs
          totalSpent { displayValue }
          verificationStatus
        }
      }
    }
  }
}
"""


def _fmt_money(val):
    """Format '100.0' → '$100', '15.5' → '$16'."""
    try:
        n = float(val)
        if n == 0:
            return ""
        return f"${int(round(n))}"
    except Exception:
        return str(val) if val else ""


def _score_job(job):
    text = (job.get("title", "") + " " + job.get("description", "")).lower()

    # ── Keyword relevance (0–6) ────────────────────────────────────────────────
    kw_raw = sum(pts for kw, pts in _SCORE_KEYWORDS.items() if kw in text)
    kw_score = min(kw_raw, 6)

    # ── Negative signals ──────────────────────────────────────────────────────
    neg = sum(pts for kw, pts in _NEGATIVE_KEYWORDS.items() if kw in text)

    # ── Gate: if paid-ads signal is weak, budget/client bonuses don't apply ───
    # Prevents high-budget irrelevant jobs (e.g. SEO, web dev, organic) from
    # scoring 7-9 purely on client quality + recency.
    if kw_score < 2:
        return max(0, min(kw_score + neg, 4))

    # ── Budget (0–2) ──────────────────────────────────────────────────────────
    budget_str = job.get("budget", "")
    engagement = job.get("engagement", "").lower()
    is_hourly = "/hr" in budget_str or "hourly" in engagement
    budget_score = 0
    try:
        num = float(
            budget_str.replace("$", "").replace(",", "").replace("/hr", "")
            .strip().split("-")[0].strip()
        )
        if is_hourly:
            budget_score = 2 if num >= 50 else (1 if num >= 25 else 0)
        else:
            budget_score = 2 if num >= 1000 else (1 if num >= 500 else 0)
    except Exception:
        pass

    # ── Client quality (0–2) ──────────────────────────────────────────────────
    client = job.get("client") or {}
    client_score = 0
    if float(client.get("totalFeedback") or 0) >= 4.5:
        client_score += 1
    if int(client.get("totalPostedJobs") or 0) >= 5:
        client_score += 1

    # ── Recency (0–1) ─────────────────────────────────────────────────────────
    recency = 0
    created = job.get("created", "")
    if created:
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - dt).total_seconds() / 3600 <= 48:
                recency = 1
        except Exception:
            pass

    total = kw_score + budget_score + client_score + recency + neg
    return max(0, min(total, 10))


def search_jobs(keywords, job_type="all", limit=30, token=None):
    """Search Upwork jobs across a list of keywords.

    Returns deduplicated, score-sorted list of job dicts:
      id, title, description, budget, engagement, skills, client, score, created
    """
    seen = {}
    for kw in keywords:
        data = _gql(
            _JOB_SEARCH_QUERY,
            {"searchExpr": kw},
            token=token,
        )
        if not data:
            continue
        postings = data.get("marketplaceJobPostingsSearch") or {}
        for edge in postings.get("edges") or []:
            node = edge.get("node") or {}
            jid = node.get("id")
            if not jid or jid in seen:
                continue

            # Budget: prefer hourly range, fall back to fixed amount
            engagement = node.get("engagement") or ""
            is_hourly = bool(node.get("hourlyBudgetType")) or bool(node.get("hourlyBudgetMin"))
            if is_hourly:
                lo_raw = (node.get("hourlyBudgetMin") or {}).get("rawValue", "")
                hi_raw = (node.get("hourlyBudgetMax") or {}).get("rawValue", "")
                lo = _fmt_money(lo_raw)
                hi = _fmt_money(hi_raw)
                if lo and hi:
                    budget = f"{lo}-{hi}/hr"
                elif lo:
                    budget = f"{lo}+/hr"
                else:
                    budget = "Hourly"
            else:
                raw = (node.get("amount") or {}).get("rawValue", "")
                budget = _fmt_money(raw) or "N/A"

            # Client info — normalise field names
            raw_client = node.get("client") or {}
            client = {
                "paymentVerificationStatus": "VERIFIED" if raw_client.get("verificationStatus") == "VERIFIED" else "",
                "totalFeedback": raw_client.get("totalFeedback", 0),
                "totalPostedJobs": raw_client.get("totalPostedJobs", 0),
                "totalSpent": {"amount": (raw_client.get("totalSpent") or {}).get("displayValue", "")},
            }

            ciphertext = node.get("ciphertext", "")
            job = {
                "id": jid,
                "title": node.get("title", ""),
                "description": node.get("description", ""),
                "budget": budget,
                "engagement": engagement,
                "skills": [s.get("name", "") for s in (node.get("skills") or [])],
                "client": client,
                "created": node.get("createdDateTime", ""),
                "url": f"https://www.upwork.com/jobs/{ciphertext}" if ciphertext else "",
            }

            if job_type == "hourly" and not is_hourly:
                continue
            if job_type == "fixed" and is_hourly:
                continue

            job["score"] = _score_job(job)
            seen[jid] = job

    return sorted(seen.values(), key=lambda j: j["score"], reverse=True)[:limit]

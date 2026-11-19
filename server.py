"""Hotel Montana – Google Reviews MCP server (remote, Streamable HTTP).

Env vars (set in Render dashboard, never in code):
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN
  GBP_ACCOUNT_ID   (e.g. 105488431171037186294)
  GBP_LOCATION_ID  (e.g. 2789815732291784272)
  MCP_SECRET       long random string; the endpoint is /<MCP_SECRET>/mcp
"""
import os
import time

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

ACCOUNT = os.environ.get("GBP_ACCOUNT_ID", "105488431171037186294")
LOCATION = os.environ.get("GBP_LOCATION_ID", "2789815732291784272")
SECRET = os.environ["MCP_SECRET"]
BASE = f"https://mybusiness.googleapis.com/v4/accounts/{ACCOUNT}/locations/{LOCATION}"
STARS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}

_token = {"value": None, "exp": 0.0}


def _access_token() -> str:
    if _token["value"] and time.time() < _token["exp"] - 60:
        return _token["value"]
    r = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "refresh_token": os.environ["GOOGLE_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        },
        timeout=20,
    )
    r.raise_for_status()
    d = r.json()
    _token.update(value=d["access_token"], exp=time.time() + d.get("expires_in", 3600))
    return _token["value"]


def _api(method: str, path: str, **kw) -> dict:
    r = httpx.request(
        method, BASE + path,
        headers={"Authorization": f"Bearer {_access_token()}"},
        timeout=30, **kw,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Google API {r.status_code}: {r.text[:500]}")
    return r.json() if r.content else {}


mcp = FastMCP(
    "Hotel Montana Reviews",
    stateless_http=True,
    streamable_http_path=f"/{SECRET}/mcp",
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8000")),
    # Render terminates TLS and forwards its own Host header.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


@mcp.tool()
def list_reviews(page_size: int = 20, page_token: str = "", only_unanswered: bool = False) -> dict:
    """List Google reviews for Hotel Montana, newest first.

    Returns rating summary, reviews (id, author, stars, comment, date, existing reply)
    and next_page_token for pagination. Set only_unanswered=True to keep only
    reviews without an owner reply (filtering is applied to the fetched page).
    """
    params = {"pageSize": max(1, min(page_size, 50)), "orderBy": "updateTime desc"}
    if page_token:
        params["pageToken"] = page_token
    d = _api("GET", "/reviews", params=params)
    out = []
    for rv in d.get("reviews", []):
        reply = rv.get("reviewReply")
        if only_unanswered and reply:
            continue
        out.append({
            "review_id": rv.get("reviewId"),
            "author": rv.get("reviewer", {}).get("displayName"),
            "stars": STARS.get(rv.get("starRating"), rv.get("starRating")),
            "comment": rv.get("comment", ""),
            "created": rv.get("createTime"),
            "reply": reply.get("comment") if reply else None,
        })
    return {
        "average_rating": d.get("averageRating"),
        "total_reviews": d.get("totalReviewCount"),
        "reviews": out,
        "next_page_token": d.get("nextPageToken"),
    }


@mcp.tool()
def reply_to_review(review_id: str, reply_text: str) -> dict:
    """Publish (or replace) the owner's public reply to a review. This is visible
    on Google immediately – only call after the user has approved the exact text."""
    if not reply_text.strip():
        raise ValueError("reply_text is empty")
    d = _api("PUT", f"/reviews/{review_id}/reply", json={"comment": reply_text.strip()})
    return {"ok": True, "reply": d.get("comment"), "updated": d.get("updateTime")}


@mcp.tool()
def delete_reply(review_id: str) -> dict:
    """Delete the owner's reply to a review (the review itself stays)."""
    _api("DELETE", f"/reviews/{review_id}/reply")
    return {"ok": True}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")

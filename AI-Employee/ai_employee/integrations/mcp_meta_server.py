"""
AI Employee — Meta Social Media MCP Server

Standalone Model Context Protocol server that exposes Facebook Page
and Instagram Business operations as MCP tools. Runs over stdio transport.

Tools exposed:
  - post_facebook       — Publish a text/link post to a Facebook Page
  - post_instagram      — Publish an image post to Instagram Business
  - get_social_metrics  — Retrieve engagement metrics for both platforms
  - generate_weekly_summary — 7-day engagement digest across FB + IG

Usage:
    python -m ai_employee.integrations.mcp_meta_server

Environment variables (loaded from .env):
    META_ACCESS_TOKEN  — Long-lived Page Access Token
    META_PAGE_ID       — Facebook Page ID
    META_IG_USER_ID    — Instagram Business Account ID
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from ai_employee.integrations.meta_client import MetaClient

# Load .env from project root
_root = Path(__file__).resolve().parent.parent.parent
_dotenv = _root / ".env"
if _dotenv.exists():
    load_dotenv(_dotenv)

# ── Instantiate the Meta client ──────────────────────────────────────

_meta = MetaClient(
    access_token=os.getenv("META_ACCESS_TOKEN", ""),
    page_id=os.getenv("META_PAGE_ID", ""),
    ig_user_id=os.getenv("META_IG_USER_ID", ""),
)

# ── Create the MCP server ────────────────────────────────────────────

mcp = FastMCP(
    "meta-social",
    instructions="Meta Graph API integration for Facebook Pages and Instagram Business accounts",
)


# ══════════════════════════════════════════════════════════════════════
#  FACEBOOK POSTING
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
def post_facebook(
    message: str,
    link: str = "",
    published: bool = True,
) -> str:
    """
    Publish a post to the connected Facebook Page.

    Args:
        message: Text content of the post.
        link: Optional URL to attach to the post.
        published: Set to false to create an unpublished/draft post.

    Returns:
        JSON with success status and post_id, or error details.
    """
    result = _meta.post_facebook(message=message, link=link, published=published)
    return json.dumps(result, indent=2)


# ══════════════════════════════════════════════════════════════════════
#  INSTAGRAM POSTING
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
def post_instagram(
    image_url: str,
    caption: str = "",
) -> str:
    """
    Publish an image post to the connected Instagram Business account.

    Instagram requires a publicly-accessible image URL. The post is
    created via the two-step container workflow (create → publish).

    Args:
        image_url: Publicly-accessible URL of the image to post.
        caption: Post caption / text overlay.

    Returns:
        JSON with success status and media_id, or error details.
    """
    result = _meta.post_instagram(image_url=image_url, caption=caption)
    return json.dumps(result, indent=2)


# ══════════════════════════════════════════════════════════════════════
#  ENGAGEMENT METRICS
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
def get_social_metrics() -> str:
    """
    Retrieve engagement metrics for both Facebook and Instagram.

    Returns a combined report including:
    - Facebook: page impressions, engaged users, post engagements,
      fan count, and recent posts with like/comment/share counts.
    - Instagram: impressions, reach, profile views, and recent media
      with like/comment counts.

    Returns:
        JSON with facebook and instagram sections plus a timestamp.
    """
    result = _meta.get_social_metrics()
    return json.dumps(result, indent=2, default=str)


# ══════════════════════════════════════════════════════════════════════
#  WEEKLY SUMMARY
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
def generate_weekly_summary() -> str:
    """
    Generate a 7-day engagement summary across Facebook and Instagram.

    Includes:
    - Per-platform breakdown: post count, likes, comments, shares, top post.
    - Combined totals: total posts and total engagements.
    - Reporting period (from/to dates).

    Returns:
        JSON weekly digest with facebook, instagram, and combined sections.
    """
    result = _meta.generate_weekly_summary()
    return json.dumps(result, indent=2, default=str)


# ══════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    mcp.run(transport="stdio")

"""
AI Employee — Twitter (X) Social Media MCP Server

Standalone Model Context Protocol server that exposes Twitter/X operations
as MCP tools. Runs over stdio transport.

Tools exposed:
  - post_tweet              — Publish a tweet
  - get_mentions            — Retrieve recent mentions
  - generate_twitter_summary — 7-day engagement digest

Usage:
    python -m ai_employee.integrations.mcp_twitter_server

Environment variables (loaded from .env):
    TWITTER_BEARER_TOKEN        — App-level Bearer Token (read)
    TWITTER_API_KEY             — OAuth 1.0a Consumer Key
    TWITTER_API_SECRET          — OAuth 1.0a Consumer Secret
    TWITTER_ACCESS_TOKEN        — OAuth 1.0a Access Token
    TWITTER_ACCESS_TOKEN_SECRET — OAuth 1.0a Access Token Secret
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from ai_employee.integrations.twitter_client import TwitterClient

# Load .env from project root
_root = Path(__file__).resolve().parent.parent.parent
_dotenv = _root / ".env"
if _dotenv.exists():
    load_dotenv(_dotenv)

# ── Instantiate the Twitter client ───────────────────────────────────

_twitter = TwitterClient(
    bearer_token=os.getenv("TWITTER_BEARER_TOKEN", ""),
    api_key=os.getenv("TWITTER_API_KEY", ""),
    api_secret=os.getenv("TWITTER_API_SECRET", ""),
    access_token=os.getenv("TWITTER_ACCESS_TOKEN", ""),
    access_token_secret=os.getenv("TWITTER_ACCESS_TOKEN_SECRET", ""),
)

# ── Create the MCP server ────────────────────────────────────────────

mcp = FastMCP(
    "twitter-social",
    instructions="Twitter/X API v2 integration for posting tweets and engagement analytics",
)


# ══════════════════════════════════════════════════════════════════════
#  TWEET POSTING
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
def post_tweet(text: str) -> str:
    """
    Publish a tweet to the connected Twitter/X account.

    Args:
        text: Tweet text content (max 280 characters).

    Returns:
        JSON with success status and tweet_id, or error details.
    """
    result = _twitter.post_tweet(text=text)
    return json.dumps(result, indent=2)


# ══════════════════════════════════════════════════════════════════════
#  MENTIONS
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
def get_mentions(max_results: int = 25) -> str:
    """
    Retrieve recent mentions of the connected Twitter/X account.

    Args:
        max_results: Number of mentions to fetch (10-100, default 25).

    Returns:
        JSON list of mentions with id, text, author_id, created_at,
        and public engagement metrics.
    """
    result = _twitter.get_mentions(max_results=max_results)
    return json.dumps(result, indent=2, default=str)


# ══════════════════════════════════════════════════════════════════════
#  WEEKLY SUMMARY
# ══════════════════════════════════════════════════════════════════════


@mcp.tool()
def generate_twitter_summary() -> str:
    """
    Generate a 7-day engagement summary for the connected Twitter/X account.

    Includes:
    - Tweet breakdown: post count, likes, retweets, replies, impressions,
      and top tweet.
    - Mention count for the period.
    - Reporting period (from/to dates).

    Returns:
        JSON weekly digest with twitter and mentions sections.
    """
    result = _twitter.generate_weekly_summary()
    return json.dumps(result, indent=2, default=str)


# ══════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    mcp.run(transport="stdio")

"""
AI Employee — Twitter (X) API v2 Client

Handles Twitter/X operations via the Twitter API v2:
  - Post tweets
  - Retrieve mentions
  - Retrieve recent tweets with engagement metrics
  - Generate weekly engagement summaries

Authentication:
  - Bearer Token for read-only endpoints (GET)
  - OAuth 1.0a (User Context) for write endpoints (POST)

Environment variables (loaded from .env):
    TWITTER_BEARER_TOKEN       — App-level Bearer Token (read)
    TWITTER_API_KEY            — OAuth 1.0a Consumer Key
    TWITTER_API_SECRET         — OAuth 1.0a Consumer Secret
    TWITTER_ACCESS_TOKEN       — OAuth 1.0a Access Token
    TWITTER_ACCESS_TOKEN_SECRET — OAuth 1.0a Access Token Secret
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from requests_oauthlib import OAuth1

logger = logging.getLogger(__name__)

_API_BASE = "https://api.twitter.com/2"


class TwitterClient:
    """Low-level wrapper around the Twitter API v2."""

    def __init__(
        self,
        bearer_token: str = "",
        api_key: str = "",
        api_secret: str = "",
        access_token: str = "",
        access_token_secret: str = "",
    ) -> None:
        self.bearer_token = bearer_token
        self.api_key = api_key
        self.api_secret = api_secret
        self.access_token = access_token
        self.access_token_secret = access_token_secret

        # Cached authenticated user ID
        self._user_id: str | None = None

    # ── helpers ───────────────────────────────────────────────────────

    def _oauth1(self) -> OAuth1:
        """Return an OAuth1 instance for user-context requests."""
        return OAuth1(
            self.api_key,
            self.api_secret,
            self.access_token,
            self.access_token_secret,
        )

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        """OAuth 1.0a user-context GET request."""
        url = f"{_API_BASE}/{endpoint}"
        resp = requests.get(url, auth=self._oauth1(), params=params or {}, timeout=30)
        data = resp.json()
        if "errors" in data:
            logger.error("Twitter API GET %s → %s", endpoint, data["errors"])
        return data

    def _post(self, endpoint: str, payload: dict | None = None) -> dict:
        """OAuth 1.0a auth POST request for write endpoints."""
        auth = OAuth1(
            self.api_key,
            self.api_secret,
            self.access_token,
            self.access_token_secret,
        )
        url = f"{_API_BASE}/{endpoint}"
        resp = requests.post(url, json=payload or {}, auth=auth, timeout=30)
        data = resp.json()
        if "errors" in data:
            logger.error("Twitter API POST %s → %s", endpoint, data["errors"])
        return data

    # ══════════════════════════════════════════════════════════════════
    #  USER IDENTITY
    # ══════════════════════════════════════════════════════════════════

    def get_me(self) -> dict:
        """
        GET /2/users/me — retrieve the authenticated user's profile.

        Caches the user ID for subsequent calls to mentions/tweets.

        Returns:
            {"id": "...", "name": "...", "username": "..."} or error dict.
        """
        if self._user_id:
            return {"id": self._user_id}

        data = self._get("users/me", {"user.fields": "id,name,username"})
        user = data.get("data", {})
        if user.get("id"):
            self._user_id = user["id"]
            logger.info("Twitter user: @%s (ID: %s)", user.get("username"), user["id"])
        return user

    def _ensure_user_id(self) -> str:
        """Get the authenticated user ID, fetching it if necessary."""
        if not self._user_id:
            self.get_me()
        return self._user_id or ""

    # ══════════════════════════════════════════════════════════════════
    #  TWEET POSTING
    # ══════════════════════════════════════════════════════════════════

    def post_tweet(self, text: str) -> dict[str, Any]:
        """
        Publish a tweet.

        Args:
            text: Tweet text content (max 280 characters).

        Returns:
            {"success": True, "tweet_id": "..."} or {"success": False, "error": "..."}.
        """
        if not text.strip():
            return {"success": False, "error": "Tweet text cannot be empty"}

        data = self._post("tweets", {"text": text})

        tweet_data = data.get("data", {})
        if tweet_data.get("id"):
            logger.info("Tweet posted: %s", tweet_data["id"])
            return {"success": True, "tweet_id": tweet_data["id"]}
        return {
            "success": False,
            "error": (
                data.get("errors", [{}])[0].get("message", "Unknown error")
                if data.get("errors")
                else data.get("detail", "Unknown error")
            ),
        }

    # ══════════════════════════════════════════════════════════════════
    #  MENTIONS
    # ══════════════════════════════════════════════════════════════════

    def get_mentions(self, max_results: int = 25) -> list[dict]:
        """
        GET /2/users/:id/mentions — retrieve recent mentions.

        Args:
            max_results: Number of mentions to retrieve (10-100).

        Returns:
            List of mention dicts with id, text, author_id, created_at.
        """
        user_id = self._ensure_user_id()
        if not user_id:
            return []

        data = self._get(
            f"users/{user_id}/mentions",
            {
                "max_results": min(max(max_results, 10), 100),
                "tweet.fields": "id,text,author_id,created_at,public_metrics",
            },
        )

        mentions = []
        for tweet in data.get("data", []):
            mentions.append({
                "id": tweet.get("id"),
                "text": tweet.get("text", ""),
                "author_id": tweet.get("author_id", ""),
                "created_at": tweet.get("created_at", ""),
                "public_metrics": tweet.get("public_metrics", {}),
            })
        return mentions

    # ══════════════════════════════════════════════════════════════════
    #  RECENT TWEETS
    # ══════════════════════════════════════════════════════════════════

    def get_recent_tweets(self, max_results: int = 25) -> list[dict]:
        """
        GET /2/users/:id/tweets — retrieve the authenticated user's recent tweets
        with engagement metrics.

        Args:
            max_results: Number of tweets to retrieve (5-100).

        Returns:
            List of tweet dicts with id, text, created_at, public_metrics.
        """
        user_id = self._ensure_user_id()
        if not user_id:
            return []

        data = self._get(
            f"users/{user_id}/tweets",
            {
                "max_results": min(max(max_results, 5), 100),
                "tweet.fields": "id,text,created_at,public_metrics",
            },
        )

        tweets = []
        for tweet in data.get("data", []):
            metrics = tweet.get("public_metrics", {})
            tweets.append({
                "id": tweet.get("id"),
                "text": (tweet.get("text") or "")[:120],
                "created_at": tweet.get("created_at", ""),
                "likes": metrics.get("like_count", 0),
                "retweets": metrics.get("retweet_count", 0),
                "replies": metrics.get("reply_count", 0),
                "impressions": metrics.get("impression_count", 0),
            })
        return tweets

    # ══════════════════════════════════════════════════════════════════
    #  WEEKLY SUMMARY
    # ══════════════════════════════════════════════════════════════════

    def generate_weekly_summary(self) -> dict[str, Any]:
        """
        Build a 7-day engagement summary for the authenticated account.

        Returns:
            {
              "period": {"from": ..., "to": ...},
              "twitter": {total_tweets, total_likes, total_retweets, total_replies,
                          total_impressions, top_tweet},
              "mentions": {total_mentions},
            }
        """
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)
        cutoff = week_ago.isoformat()

        # Fetch recent tweets
        tweets = self.get_recent_tweets(max_results=100)
        week_tweets = [t for t in tweets if (t.get("created_at") or "") >= cutoff]

        total_likes = sum(t.get("likes", 0) for t in week_tweets)
        total_retweets = sum(t.get("retweets", 0) for t in week_tweets)
        total_replies = sum(t.get("replies", 0) for t in week_tweets)
        total_impressions = sum(t.get("impressions", 0) for t in week_tweets)

        top_tweet = max(
            week_tweets,
            key=lambda t: t.get("likes", 0) + t.get("retweets", 0) + t.get("replies", 0),
            default=None,
        )

        # Fetch recent mentions
        mentions = self.get_mentions(max_results=100)
        week_mentions = [m for m in mentions if (m.get("created_at") or "") >= cutoff]

        return {
            "period": {
                "from": week_ago.strftime("%Y-%m-%d"),
                "to": now.strftime("%Y-%m-%d"),
            },
            "twitter": {
                "total_tweets": len(week_tweets),
                "total_likes": total_likes,
                "total_retweets": total_retweets,
                "total_replies": total_replies,
                "total_impressions": total_impressions,
                "top_tweet": top_tweet,
            },
            "mentions": {
                "total_mentions": len(week_mentions),
            },
        }

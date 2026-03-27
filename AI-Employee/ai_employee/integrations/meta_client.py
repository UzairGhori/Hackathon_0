"""
AI Employee — Meta Graph API Client

Handles Facebook Page and Instagram Business account operations
via the Meta Graph API v21.0.

Capabilities:
  - Post to Facebook Pages
  - Post to Instagram (image-based via container workflow)
  - Retrieve engagement metrics (page-level and post-level)
  - Generate weekly engagement summaries

Environment variables (loaded from .env):
    META_ACCESS_TOKEN  — Long-lived Page Access Token
    META_PAGE_ID       — Facebook Page ID
    META_IG_USER_ID    — Instagram Business Account ID
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.facebook.com/v21.0"


class MetaClient:
    """Low-level wrapper around the Meta Graph API."""

    def __init__(
        self,
        access_token: str,
        page_id: str = "",
        ig_user_id: str = "",
    ) -> None:
        self.access_token = access_token
        self.page_id = page_id
        self.ig_user_id = ig_user_id

    # ── helpers ───────────────────────────────────────────────────────

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        params = params or {}
        params["access_token"] = self.access_token
        url = f"{_GRAPH_BASE}/{endpoint}"
        resp = requests.get(url, params=params, timeout=30)
        data = resp.json()
        if "error" in data:
            logger.error("Meta API GET %s → %s", endpoint, data["error"])
        return data

    def _post(self, endpoint: str, payload: dict | None = None) -> dict:
        payload = payload or {}
        payload["access_token"] = self.access_token
        url = f"{_GRAPH_BASE}/{endpoint}"
        resp = requests.post(url, data=payload, timeout=30)
        data = resp.json()
        if "error" in data:
            logger.error("Meta API POST %s → %s", endpoint, data["error"])
        return data

    # ══════════════════════════════════════════════════════════════════
    #  FACEBOOK POSTING
    # ══════════════════════════════════════════════════════════════════

    def post_facebook(
        self,
        message: str,
        link: str = "",
        published: bool = True,
    ) -> dict[str, Any]:
        """
        Publish a post to the Facebook Page.

        Args:
            message:   Text content of the post.
            link:      Optional URL to attach.
            published: False to create an unpublished/scheduled post.

        Returns:
            {"success": True, "post_id": "..."} or {"success": False, "error": "..."}.
        """
        if not self.page_id:
            return {"success": False, "error": "META_PAGE_ID not configured"}

        payload: dict[str, Any] = {
            "message": message,
            "published": str(published).lower(),
        }
        if link:
            payload["link"] = link

        data = self._post(f"{self.page_id}/feed", payload)

        if "id" in data:
            logger.info("Facebook post created: %s", data["id"])
            return {"success": True, "post_id": data["id"]}
        return {
            "success": False,
            "error": data.get("error", {}).get("message", "Unknown error"),
        }

    # ══════════════════════════════════════════════════════════════════
    #  INSTAGRAM POSTING  (image container workflow)
    # ══════════════════════════════════════════════════════════════════

    def post_instagram(
        self,
        image_url: str,
        caption: str = "",
    ) -> dict[str, Any]:
        """
        Publish an image post to Instagram Business account.

        Instagram requires a two-step flow:
          1. Create a media container with the image URL.
          2. Publish that container.

        Args:
            image_url: Publicly-accessible URL of the image.
            caption:   Post caption / text.

        Returns:
            {"success": True, "media_id": "..."} or {"success": False, "error": "..."}.
        """
        if not self.ig_user_id:
            return {"success": False, "error": "META_IG_USER_ID not configured"}

        # Step 1 — create media container
        container = self._post(
            f"{self.ig_user_id}/media",
            {"image_url": image_url, "caption": caption},
        )
        creation_id = container.get("id")
        if not creation_id:
            return {
                "success": False,
                "error": container.get("error", {}).get("message", "Container creation failed"),
            }

        # Step 2 — publish the container
        publish = self._post(
            f"{self.ig_user_id}/media_publish",
            {"creation_id": creation_id},
        )
        media_id = publish.get("id")
        if media_id:
            logger.info("Instagram post published: %s", media_id)
            return {"success": True, "media_id": media_id}
        return {
            "success": False,
            "error": publish.get("error", {}).get("message", "Publish failed"),
        }

    # ══════════════════════════════════════════════════════════════════
    #  ENGAGEMENT METRICS
    # ══════════════════════════════════════════════════════════════════

    def _fb_page_insights(self, metrics: list[str], period: str = "day", days: int = 7) -> dict:
        """Fetch Facebook Page-level insights."""
        if not self.page_id:
            return {"error": "META_PAGE_ID not configured"}

        now = datetime.now(timezone.utc)
        since = int((now - timedelta(days=days)).timestamp())
        until = int(now.timestamp())

        data = self._get(
            f"{self.page_id}/insights",
            {
                "metric": ",".join(metrics),
                "period": period,
                "since": since,
                "until": until,
            },
        )
        return data

    def _ig_user_insights(self, metrics: list[str], period: str = "day", days: int = 7) -> dict:
        """Fetch Instagram account-level insights."""
        if not self.ig_user_id:
            return {"error": "META_IG_USER_ID not configured"}

        now = datetime.now(timezone.utc)
        since = int((now - timedelta(days=days)).timestamp())
        until = int(now.timestamp())

        data = self._get(
            f"{self.ig_user_id}/insights",
            {
                "metric": ",".join(metrics),
                "period": period,
                "since": since,
                "until": until,
            },
        )
        return data

    def _fb_recent_posts(self, limit: int = 25) -> list[dict]:
        """Return recent Facebook Page posts with basic metrics."""
        if not self.page_id:
            return []
        data = self._get(
            f"{self.page_id}/posts",
            {"fields": "id,message,created_time,shares,likes.summary(true),comments.summary(true)", "limit": limit},
        )
        posts = []
        for p in data.get("data", []):
            posts.append({
                "id": p.get("id"),
                "message": (p.get("message") or "")[:120],
                "created_time": p.get("created_time"),
                "likes": p.get("likes", {}).get("summary", {}).get("total_count", 0),
                "comments": p.get("comments", {}).get("summary", {}).get("total_count", 0),
                "shares": p.get("shares", {}).get("count", 0),
            })
        return posts

    def _ig_recent_media(self, limit: int = 25) -> list[dict]:
        """Return recent Instagram media with basic metrics."""
        if not self.ig_user_id:
            return []
        data = self._get(
            f"{self.ig_user_id}/media",
            {"fields": "id,caption,timestamp,like_count,comments_count,media_type", "limit": limit},
        )
        media = []
        for m in data.get("data", []):
            media.append({
                "id": m.get("id"),
                "caption": (m.get("caption") or "")[:120],
                "timestamp": m.get("timestamp"),
                "likes": m.get("like_count", 0),
                "comments": m.get("comments_count", 0),
                "media_type": m.get("media_type"),
            })
        return media

    def get_social_metrics(self) -> dict[str, Any]:
        """
        Retrieve combined engagement metrics for both platforms.

        Returns dict with keys: facebook, instagram, retrieved_at.
        """
        fb_metrics = {}
        ig_metrics = {}

        # ── Facebook ──
        if self.page_id:
            insights = self._fb_page_insights(
                ["page_impressions", "page_engaged_users", "page_post_engagements", "page_fans"],
                period="day",
                days=7,
            )
            fb_data: dict[str, Any] = {}
            for entry in insights.get("data", []):
                name = entry.get("name", "")
                values = entry.get("values", [])
                fb_data[name] = [{"end_time": v.get("end_time"), "value": v.get("value")} for v in values]
            fb_metrics = {
                "insights": fb_data,
                "recent_posts": self._fb_recent_posts(limit=10),
            }

        # ── Instagram ──
        if self.ig_user_id:
            insights = self._ig_user_insights(
                ["impressions", "reach", "profile_views"],
                period="day",
                days=7,
            )
            ig_data: dict[str, Any] = {}
            for entry in insights.get("data", []):
                name = entry.get("name", "")
                values = entry.get("values", [])
                ig_data[name] = [{"end_time": v.get("end_time"), "value": v.get("value")} for v in values]
            ig_metrics = {
                "insights": ig_data,
                "recent_media": self._ig_recent_media(limit=10),
            }

        return {
            "facebook": fb_metrics,
            "instagram": ig_metrics,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        }

    # ══════════════════════════════════════════════════════════════════
    #  WEEKLY SUMMARY
    # ══════════════════════════════════════════════════════════════════

    def generate_weekly_summary(self) -> dict[str, Any]:
        """
        Build a 7-day engagement summary across Facebook and Instagram.

        Returns:
            {
              "period": {"from": ..., "to": ...},
              "facebook": {total_posts, total_likes, total_comments, total_shares, top_post},
              "instagram": {total_posts, total_likes, total_comments, top_post},
              "combined": {total_posts, total_engagements},
            }
        """
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)
        cutoff = week_ago.isoformat()

        summary: dict[str, Any] = {
            "period": {
                "from": week_ago.strftime("%Y-%m-%d"),
                "to": now.strftime("%Y-%m-%d"),
            },
            "facebook": {},
            "instagram": {},
            "combined": {"total_posts": 0, "total_engagements": 0},
        }

        # ── Facebook ──
        fb_posts = self._fb_recent_posts(limit=50)
        fb_week = [p for p in fb_posts if (p.get("created_time") or "") >= cutoff]
        fb_likes = sum(p.get("likes", 0) for p in fb_week)
        fb_comments = sum(p.get("comments", 0) for p in fb_week)
        fb_shares = sum(p.get("shares", 0) for p in fb_week)
        fb_top = max(fb_week, key=lambda p: p.get("likes", 0) + p.get("comments", 0) + p.get("shares", 0), default=None)

        summary["facebook"] = {
            "total_posts": len(fb_week),
            "total_likes": fb_likes,
            "total_comments": fb_comments,
            "total_shares": fb_shares,
            "top_post": fb_top,
        }

        # ── Instagram ──
        ig_media = self._ig_recent_media(limit=50)
        ig_week = [m for m in ig_media if (m.get("timestamp") or "") >= cutoff]
        ig_likes = sum(m.get("likes", 0) for m in ig_week)
        ig_comments = sum(m.get("comments", 0) for m in ig_week)
        ig_top = max(ig_week, key=lambda m: m.get("likes", 0) + m.get("comments", 0), default=None)

        summary["instagram"] = {
            "total_posts": len(ig_week),
            "total_likes": ig_likes,
            "total_comments": ig_comments,
            "top_post": ig_top,
        }

        # ── Combined ──
        total_posts = len(fb_week) + len(ig_week)
        total_eng = fb_likes + fb_comments + fb_shares + ig_likes + ig_comments
        summary["combined"] = {
            "total_posts": total_posts,
            "total_engagements": total_eng,
        }

        return summary

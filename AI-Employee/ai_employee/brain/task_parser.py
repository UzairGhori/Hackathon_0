"""
AI Employee — Task Parser (Claude API-Powered)

Reads a raw markdown task file and extracts structured metadata
using Claude's language understanding.

Extracts:
    - sender       (who submitted the task)
    - deadline     (when it's due)
    - description  (what needs to be done)
    - required_action (the specific action to take)
    - recipients   (who should receive the output)
    - attachments  (referenced files or links)

Input:  Raw markdown string
Output: ParsedTask dataclass / JSON dict

Falls back to regex-based extraction if Claude API is unavailable.
"""

import json
import logging
import re
from dataclasses import dataclass, asdict, field
from datetime import datetime

import anthropic

log = logging.getLogger("ai_employee.parser")

MODEL = "claude-sonnet-4-5-20250929"


@dataclass
class ParsedTask:
    """Structured metadata extracted from a raw task file."""
    task_id: str
    title: str
    sender: str
    deadline: str
    description: str
    required_action: str
    recipients: list[str] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    raw_content: str = ""
    parsed_at: str = field(default_factory=lambda: datetime.now().isoformat())
    parse_method: str = "local"    # "claude" or "local"

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


PARSE_PROMPT = """You are a Task Parser inside an AI Employee system.

Analyze the following markdown task and extract structured metadata.
Be precise. If a field is not explicitly stated, infer it if possible,
otherwise use "Not specified".

Respond ONLY with valid JSON in this exact format (no markdown, no backticks):
{
    "title": "<task title from first heading or inferred>",
    "sender": "<who submitted/wrote the task, or 'Not specified'>",
    "deadline": "<deadline date/time if mentioned, or 'Not specified'>",
    "description": "<1-2 sentence summary of what the task is about>",
    "required_action": "<the specific action that must be taken>",
    "recipients": ["<email or name of who should receive the output>"],
    "attachments": ["<any referenced files, links, or documents>"]
}

TASK CONTENT:
"""


class TaskParser:
    """Parses markdown tasks into structured metadata using Claude API."""

    def __init__(self, api_key: str = ""):
        self._api_key = api_key
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else None

    @property
    def ai_enabled(self) -> bool:
        return self._client is not None

    def parse(self, content: str, task_id: str = "",
              filename: str = "") -> ParsedTask:
        """
        Parse a markdown task file into a structured ParsedTask.
        Uses Claude API if available, else falls back to regex.
        """
        if not task_id:
            task_id = filename.replace(".md", "") if filename else "unknown"

        if self._client:
            try:
                return self._parse_with_claude(content, task_id)
            except Exception as exc:
                log.warning("Claude API parsing failed, using fallback: %s", exc)

        return self._parse_with_regex(content, task_id)

    # ── Claude API path ──────────────────────────────────────────────────

    def _parse_with_claude(self, content: str, task_id: str) -> ParsedTask:
        log.debug("Parsing with Claude API...")

        response = self._client.messages.create(
            model=MODEL,
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": PARSE_PROMPT + content,
            }],
        )

        raw = response.content[0].text.strip()
        data = json.loads(raw)

        result = ParsedTask(
            task_id=task_id,
            title=data.get("title", "Untitled Task"),
            sender=data.get("sender", "Not specified"),
            deadline=data.get("deadline", "Not specified"),
            description=data.get("description", ""),
            required_action=data.get("required_action", ""),
            recipients=data.get("recipients", []),
            attachments=data.get("attachments", []),
            raw_content=content,
            parse_method="claude",
        )

        log.info(
            "Claude parsed: '%s' | sender=%s | deadline=%s | action=%s",
            result.title, result.sender, result.deadline,
            result.required_action[:60],
        )
        return result

    # ── Regex fallback path ──────────────────────────────────────────────

    def _parse_with_regex(self, content: str, task_id: str) -> ParsedTask:
        title = self._extract_title(content, task_id)
        sender = self._extract_sender(content)
        deadline = self._extract_deadline(content)
        description = self._extract_description(content)
        action = self._extract_action(content)
        recipients = self._extract_emails(content)
        attachments = self._extract_attachments(content)

        result = ParsedTask(
            task_id=task_id,
            title=title,
            sender=sender,
            deadline=deadline,
            description=description,
            required_action=action,
            recipients=recipients,
            attachments=attachments,
            raw_content=content,
            parse_method="local",
        )

        log.info("Local parsed: '%s' | action=%s", result.title, action[:60])
        return result

    # ── Extraction helpers ───────────────────────────────────────────────

    @staticmethod
    def _extract_title(text: str, fallback: str) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
        return fallback.replace("_", " ").replace("-", " ").title()

    @staticmethod
    def _extract_sender(text: str) -> str:
        patterns = [
            r"(?i)(?:from|sender|by|author)\s*:\s*(.+)",
            r"(?i)(?:submitted by|assigned by|requested by)\s*:?\s*(.+)",
        ]
        for pat in patterns:
            match = re.search(pat, text)
            if match:
                return match.group(1).strip()
        return "Not specified"

    @staticmethod
    def _extract_deadline(text: str) -> str:
        patterns = [
            r"(?i)(?:deadline|due|by|before|until)\s*:\s*(.+)",
            r"(?i)(?:by end of|before|no later than)\s+(.+?)[\.\n]",
            r"(\d{4}-\d{2}-\d{2})",
            r"(?i)(today|tomorrow|end of day|end of week|next monday|next friday)",
        ]
        for pat in patterns:
            match = re.search(pat, text)
            if match:
                return match.group(1).strip()
        return "Not specified"

    @staticmethod
    def _extract_description(text: str) -> str:
        lines = text.strip().splitlines()
        for line in lines:
            cleaned = line.strip()
            if not cleaned or cleaned.startswith("#") or cleaned.startswith("-"):
                continue
            if re.match(r"^[A-Za-z ]+:$", cleaned):
                continue
            return cleaned
        return "No description could be extracted."

    @staticmethod
    def _extract_action(text: str) -> str:
        # Look for explicit action lines
        patterns = [
            r"(?i)(?:action|task|todo|required)\s*:\s*(.+)",
            r"(?i)(?:please|need to|must|should)\s+(.+?)[\.\n]",
        ]
        for pat in patterns:
            match = re.search(pat, text)
            if match:
                return match.group(1).strip()

        # Fall back to first imperative sentence
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("-"):
                if any(stripped.lower().startswith(v) for v in [
                    "write", "draft", "send", "create", "reply", "summarize",
                    "review", "analyze", "schedule", "plan", "fix", "update",
                    "post", "share", "prepare", "compile", "generate",
                ]):
                    return stripped
        return "Review and process the task."

    @staticmethod
    def _extract_emails(text: str) -> list[str]:
        return re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)

    @staticmethod
    def _extract_attachments(text: str) -> list[str]:
        links = re.findall(r"https?://\S+", text)
        files = re.findall(r"[\w/-]+\.(?:pdf|docx?|xlsx?|csv|png|jpg|zip)", text)
        return links + files

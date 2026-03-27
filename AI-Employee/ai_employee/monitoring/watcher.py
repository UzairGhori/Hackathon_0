"""
AI Employee — Inbox Watcher (Gold Tier)

Real-time file system monitor that watches vault/Inbox/ for new tasks
and feeds them into the autonomous pipeline.

Upgraded from Bronze tier:
  - Integrates with DecisionEngine for intelligent routing
  - Feeds tasks to the correct agent
  - Records everything in Memory
"""

import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

log = logging.getLogger("ai_employee.watcher")


def read_with_retry(filepath: str, max_retries: int = 10,
                    delay: float = 0.5) -> str:
    """
    Read a file with retries — handles Windows file locking and
    delayed writes that occur when files are created externally.
    """
    for attempt in range(1, max_retries + 1):
        try:
            content = Path(filepath).read_text(encoding="utf-8")
            if content.strip():
                return content
            log.debug("Attempt %d/%d — file empty, waiting...", attempt, max_retries)
            time.sleep(delay)
        except PermissionError:
            log.debug("Attempt %d/%d — file locked, waiting...", attempt, max_retries)
            time.sleep(delay)
        except Exception as exc:
            log.error("Could not read %s: %s", filepath, exc)
            return ""
    return ""


def summarize_content(text: str) -> str:
    """Build a quick summary from raw markdown text."""
    lines = text.strip().splitlines()

    title = None
    for line in lines:
        if line.strip().startswith("#"):
            title = line.strip().lstrip("#").strip()
            break

    total_lines = len(lines)
    non_empty = len([l for l in lines if l.strip()])
    word_count = len(text.split())

    headings = [l.strip().lstrip("#").strip() for l in lines if l.strip().startswith("#")]

    preview_lines = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")][:5]
    preview = "\n> ".join(preview_lines) if preview_lines else "_No body text found._"

    parts = []
    if title:
        parts.append(f"**Detected title:** {title}")
    parts.append(f"**Word count:** {word_count}")
    parts.append(f"**Lines:** {non_empty} non-empty / {total_lines} total")
    if headings:
        parts.append("**Outline:**\n" + "\n".join(f"  - {h}" for h in headings))
    parts.append(f"**Preview:**\n> {preview}")

    return "\n".join(parts)


def process_file(filepath: str, output_dir: Path | None = None) -> Path | None:
    """
    Read a markdown file from Inbox, build a triage response,
    and write it into Needs_Action.

    Returns the output path or None on failure.
    """
    path = Path(filepath)
    filename = path.name
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    content = read_with_retry(filepath)
    if not content.strip():
        log.info("Skipping empty file: %s", filename)
        return None

    summary = summarize_content(content)

    if output_dir is None:
        output_dir = path.parent.parent / "Needs_Action"

    output_dir.mkdir(parents=True, exist_ok=True)
    response_name = f"Response_{filename}"
    response_path = output_dir / response_name

    response_md = f"""# Action Required — {filename}

---

| Field          | Value                  |
|----------------|------------------------|
| Source file     | `Inbox/{filename}`     |
| Received        | {timestamp}            |
| Status          | Needs Action           |

---

## Summary

{summary}

---

## Original Content

```markdown
{content}
```

---

## Next Steps

- [ ] Review the summary above
- [ ] Take the required action
- [ ] Move this note to `Done/` when finished
"""

    try:
        response_path.write_text(response_md, encoding="utf-8")
        log.info("Triaged: %s -> %s", filename, response_name)
        return response_path
    except OSError as exc:
        log.error("Could not write %s: %s", response_path, exc)
        return None


class InboxWatcher:
    """
    Watches vault/Inbox/ for new .md files using watchdog.
    Calls the provided callback for each new file detected.
    """

    def __init__(self, inbox_dir: Path, on_new_file: Callable[[str], None]):
        self._inbox_dir = inbox_dir
        self._on_new_file = on_new_file
        self._processed: set[str] = set()
        self._observer = None

    def start(self) -> None:
        """Start watching the inbox directory."""
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError:
            log.error("watchdog not installed. Run: pip install watchdog")
            return

        watcher = self

        class Handler(FileSystemEventHandler):
            def _handle(self, event):
                if event.is_directory or not event.src_path.endswith(".md"):
                    return
                filepath = os.path.abspath(event.src_path)
                if filepath in watcher._processed:
                    return
                watcher._processed.add(filepath)
                time.sleep(1)  # let OS finish writing
                watcher._on_new_file(filepath)

            def on_created(self, event):
                self._handle(event)

            def on_modified(self, event):
                self._handle(event)

        self._inbox_dir.mkdir(parents=True, exist_ok=True)
        self._observer = Observer()
        self._observer.schedule(Handler(), str(self._inbox_dir), recursive=False)
        self._observer.start()
        log.info("Watcher started: %s", self._inbox_dir)

    def stop(self) -> None:
        """Stop the watcher."""
        if self._observer:
            self._observer.stop()
            self._observer.join()
            log.info("Watcher stopped")

    def scan_existing(self) -> list[str]:
        """Return paths to all .md files currently in inbox."""
        if not self._inbox_dir.exists():
            return []
        return sorted(
            str(p) for p in self._inbox_dir.iterdir()
            if p.suffix == ".md"
        )

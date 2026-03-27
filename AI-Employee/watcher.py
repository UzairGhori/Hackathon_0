"""
AI Employee — Inbox Watcher (Bronze Tier)

Monitors vault/Inbox/ for new .md files.
When a new file appears, it reads the content, generates a simple
summary, and writes a response file into vault/Needs_Action/.

Usage:
    pip install watchdog
    python watcher.py
"""

import os
import time
from datetime import datetime

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ---------------------------------------------------------------------------
# Configuration — paths are relative to where this script lives
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INBOX_DIR = os.path.join(SCRIPT_DIR, "vault", "Inbox")
NEEDS_ACTION_DIR = os.path.join(SCRIPT_DIR, "vault", "Needs_Action")


def summarize_content(text: str) -> str:
    """
    Build a simple summary from the raw markdown text.
    No external APIs — just basic text extraction.
    """
    lines = text.strip().splitlines()

    # Grab the first heading (line starting with #) as the detected title
    title = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            break

    # Count basic stats
    total_lines = len(lines)
    non_empty_lines = len([l for l in lines if l.strip()])
    word_count = len(text.split())

    # Collect every heading for a quick outline
    headings = [
        l.strip().lstrip("#").strip()
        for l in lines
        if l.strip().startswith("#")
    ]

    # Take the first 5 non-empty, non-heading lines as a preview
    preview_lines = [
        l.strip()
        for l in lines
        if l.strip() and not l.strip().startswith("#")
    ][:5]
    preview = "\n> ".join(preview_lines) if preview_lines else "_No body text found._"

    # Assemble the summary
    summary_parts = []
    if title:
        summary_parts.append(f"**Detected title:** {title}")
    summary_parts.append(f"**Word count:** {word_count}")
    summary_parts.append(f"**Lines:** {non_empty_lines} non-empty / {total_lines} total")

    if headings:
        heading_list = "\n".join(f"  - {h}" for h in headings)
        summary_parts.append(f"**Outline:**\n{heading_list}")

    summary_parts.append(f"**Preview:**\n> {preview}")

    return "\n".join(summary_parts)


def read_with_retry(filepath: str, max_retries: int = 10, delay: float = 0.5) -> str:
    """
    Try reading the file multiple times, waiting between attempts.
    On Windows, files are often created empty first and content is
    written shortly after — so we retry until content appears.
    """
    for attempt in range(1, max_retries + 1):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
            if content.strip():
                return content
            # File exists but is still empty — wait and retry
            print(f"[WAIT]  Attempt {attempt}/{max_retries} — file empty, waiting...")
            time.sleep(delay)
        except PermissionError:
            # File might still be locked by another process
            print(f"[WAIT]  Attempt {attempt}/{max_retries} — file locked, waiting...")
            time.sleep(delay)
        except Exception as exc:
            print(f"[ERROR] Could not read {filepath}: {exc}")
            return ""
    return ""


def process_file(filepath: str) -> None:
    """
    Read a markdown file from Inbox, build a response note,
    and write it into Needs_Action.
    """
    filename = os.path.basename(filepath)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Read the original content (with retries for Windows timing)
    content = read_with_retry(filepath)

    # Skip if still empty after all retries
    if not content.strip():
        print(f"[SKIP]  {filename} is empty after retries, ignoring.")
        return

    # Generate the summary
    summary = summarize_content(content)

    # Build the response markdown
    response_name = f"Response_{filename}"
    response_path = os.path.join(NEEDS_ACTION_DIR, response_name)

    response_md = (
        f"# Action Required — {filename}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"| Field          | Value                  |\n"
        f"|----------------|------------------------|\n"
        f"| Source file     | `Inbox/{filename}`     |\n"
        f"| Received        | {timestamp}            |\n"
        f"| Status          | Needs Action           |\n"
        f"\n"
        f"---\n"
        f"\n"
        f"## Summary\n"
        f"\n"
        f"{summary}\n"
        f"\n"
        f"---\n"
        f"\n"
        f"## Original Content\n"
        f"\n"
        f"```markdown\n"
        f"{content}\n"
        f"```\n"
        f"\n"
        f"---\n"
        f"\n"
        f"## Next Steps\n"
        f"\n"
        f"- [ ] Review the summary above\n"
        f"- [ ] Take the required action\n"
        f"- [ ] Move this note to `Done/` when finished\n"
    )

    # Write the response file
    try:
        with open(response_path, "w", encoding="utf-8") as f:
            f.write(response_md)
        print(f"[DONE]  {filename}  -->  Needs_Action/{response_name}")
    except Exception as exc:
        print(f"[ERROR] Could not write {response_path}: {exc}")


# ---------------------------------------------------------------------------
# Watchdog event handler — reacts to new files in Inbox/
# ---------------------------------------------------------------------------
class InboxHandler(FileSystemEventHandler):
    """Handles file-system events inside vault/Inbox/."""

    def __init__(self):
        super().__init__()
        # Track which files we already processed so we don't do them twice
        self._processed = set()

    def _handle(self, event):
        """Common handler for both created and modified events."""
        # Ignore directories and non-markdown files
        if event.is_directory:
            return
        if not event.src_path.endswith(".md"):
            return

        # Skip if we already processed this file
        filepath = os.path.abspath(event.src_path)
        if filepath in self._processed:
            return

        print(f"[NEW]   Detected: {os.path.basename(filepath)}")

        # Mark as processed immediately to avoid duplicate runs
        self._processed.add(filepath)

        # Small initial delay so the OS finishes writing
        time.sleep(0.5)

        process_file(filepath)

    def on_created(self, event):
        """Fired when a new file appears in the watched folder."""
        self._handle(event)

    def on_modified(self, event):
        """Fallback — fired when file content is written after creation."""
        self._handle(event)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def main():
    # Make sure the watched directories exist
    os.makedirs(INBOX_DIR, exist_ok=True)
    os.makedirs(NEEDS_ACTION_DIR, exist_ok=True)

    # Set up the observer to watch Inbox/
    handler = InboxHandler()
    observer = Observer()
    observer.schedule(handler, path=INBOX_DIR, recursive=False)
    observer.start()

    print("=" * 50)
    print("  AI Employee — Inbox Watcher (Bronze Tier)")
    print("=" * 50)
    print(f"  Watching : {INBOX_DIR}")
    print(f"  Output   : {NEEDS_ACTION_DIR}")
    print("  Press Ctrl+C to stop.")
    print("=" * 50)

    try:
        # Keep the script alive until interrupted
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[STOP]  Shutting down watcher...")
        observer.stop()

    observer.join()
    print("[EXIT]  Watcher stopped.")


if __name__ == "__main__":
    main()

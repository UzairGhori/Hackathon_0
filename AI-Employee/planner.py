"""
AI Employee — Reasoning Planner (Bronze Tier)

Reads every unprocessed .md file in vault/Inbox/, reasons about it,
and writes a Plan file into vault/Needs_Action/.

This agent does NOT execute tasks. It only creates plans.

Usage:
    python planner.py
"""

import os
import re
from datetime import datetime

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INBOX_DIR = os.path.join(SCRIPT_DIR, "vault", "Inbox")
NEEDS_ACTION_DIR = os.path.join(SCRIPT_DIR, "vault", "Needs_Action")

# ---------------------------------------------------------------------------
# Keywords used to detect intent and priority
# ---------------------------------------------------------------------------
HIGH_PRIORITY_KEYWORDS = [
    "urgent", "asap", "immediately", "critical", "deadline", "important",
    "emergency", "right away", "top priority",
]
HUMAN_APPROVAL_KEYWORDS = [
    "send", "email", "publish", "post", "submit", "announce", "release",
    "deploy", "payment", "invoice", "contract", "delete", "remove",
]
ACTION_VERBS = {
    "write":     "Content Creation",
    "draft":     "Content Creation",
    "compose":   "Content Creation",
    "create":    "Content Creation",
    "reply":     "Communication",
    "respond":   "Communication",
    "answer":    "Communication",
    "email":     "Communication",
    "summarize": "Analysis",
    "review":    "Analysis",
    "analyze":   "Analysis",
    "schedule":  "Planning",
    "plan":      "Planning",
    "organize":  "Planning",
    "fix":       "Maintenance",
    "update":    "Maintenance",
    "change":    "Maintenance",
}


def extract_title(text: str, filename: str) -> str:
    """Pull the first markdown heading, or fall back to the filename."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return filename.replace(".md", "").replace("_", " ").replace("-", " ").title()


def detect_objective(text: str) -> str:
    """Return the first meaningful non-heading paragraph as the objective."""
    lines = text.strip().splitlines()
    for line in lines:
        cleaned = line.strip()
        # Skip headings, blank lines, and bullet-only lines
        if not cleaned or cleaned.startswith("#") or cleaned.startswith("-"):
            continue
        # Skip label lines like "Task:" or "Details:" alone
        if re.match(r"^[A-Za-z ]+:$", cleaned):
            continue
        return cleaned
    return "Objective could not be determined from the file content."


def detect_category(text: str) -> str:
    """Match the dominant action verb to a task category."""
    lower = text.lower()
    for verb, category in ACTION_VERBS.items():
        if verb in lower:
            return category
    return "General"


def build_steps(text: str) -> list[str]:
    """
    Generate a step-by-step plan based on what the task asks for.
    Reads bullet points / requirements from the original file and
    wraps them into actionable plan steps.
    """
    steps = ["Read and understand the full task description from Inbox."]

    # Extract every bullet point from the original file
    bullets = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") and not stripped.startswith("- ["):
            bullet_text = stripped.lstrip("- ").strip()
            bullets.append(bullet_text)

    if bullets:
        steps.append("Identify the key requirements:")
        for b in bullets:
            steps.append(f"  → {b}")

    category = detect_category(text)
    if category == "Content Creation":
        steps.append("Draft the requested content following all listed requirements.")
        steps.append("Review the draft for tone, clarity, and completeness.")
    elif category == "Communication":
        steps.append("Draft the response/reply addressing all points raised.")
        steps.append("Ensure the tone matches the requested style (professional, friendly, etc.).")
    elif category == "Analysis":
        steps.append("Gather and review all relevant source material.")
        steps.append("Produce a structured summary or analysis.")
    elif category == "Planning":
        steps.append("Outline the schedule or plan with clear milestones.")
        steps.append("Identify dependencies and blockers.")
    elif category == "Maintenance":
        steps.append("Locate the item that needs fixing or updating.")
        steps.append("Apply the required changes and verify correctness.")
    else:
        steps.append("Break the task into smaller sub-tasks and complete them in order.")

    steps.append("Write the final output as a markdown file.")
    steps.append("Place the output in vault/Needs_Action/ for human review.")

    return steps


def detect_priority(text: str) -> str:
    """Assign priority based on keyword presence."""
    lower = text.lower()
    for kw in HIGH_PRIORITY_KEYWORDS:
        if kw in lower:
            return "High"
    # If the file has many requirements (4+), bump to Medium
    bullet_count = sum(1 for line in text.splitlines() if line.strip().startswith("- "))
    if bullet_count >= 4:
        return "Medium"
    return "Low"


def needs_human_approval(text: str) -> str:
    """Check if the task involves external-facing or destructive actions."""
    lower = text.lower()
    for kw in HUMAN_APPROVAL_KEYWORDS:
        if kw in lower:
            return "Yes"
    return "No"


def suggest_output(text: str, title: str) -> str:
    """Describe what the final deliverable should look like."""
    category = detect_category(text)
    suggestions = {
        "Content Creation": f"A polished markdown file containing the requested content titled \"{title}\".",
        "Communication":    f"A ready-to-send message or reply addressing all points in \"{title}\".",
        "Analysis":         f"A structured summary or report based on \"{title}\".",
        "Planning":         f"A timeline or action plan with milestones for \"{title}\".",
        "Maintenance":      f"An updated or fixed version of the item described in \"{title}\".",
    }
    return suggestions.get(category,
        f"A completed markdown deliverable for \"{title}\", ready for human review.")


def create_plan(filepath: str) -> str | None:
    """
    Read one Inbox file and produce a Plan markdown file
    in Needs_Action. Returns the output path, or None on failure.
    """
    filename = os.path.basename(filepath)
    timestamp_label = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    timestamp_file = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Read content
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as exc:
        print(f"[ERROR] Cannot read {filepath}: {exc}")
        return None

    if not content.strip():
        print(f"[SKIP]  {filename} is empty.")
        return None

    # --- Reasoning ---
    title       = extract_title(content, filename)
    objective   = detect_objective(content)
    category    = detect_category(content)
    steps       = build_steps(content)
    priority    = detect_priority(content)
    approval    = needs_human_approval(content)
    suggestion  = suggest_output(content, title)

    # Format the step list
    steps_md = "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))

    # --- Build the plan file ---
    plan_md = f"""# Task Plan — {title}

---

| Field                    | Value                        |
|--------------------------|------------------------------|
| Source file               | `Inbox/{filename}`          |
| Plan created              | {timestamp_label}           |
| Category                  | {category}                  |
| Priority                  | {priority}                  |
| Requires Human Approval?  | {approval}                  |

---

## Original Task

```markdown
{content.strip()}
```

---

## Objective

{objective}

---

## Step-by-Step Plan

{steps_md}

---

## Priority: {priority}

{"This task contains urgency indicators and should be handled first." if priority == "High" else ""}{"This task has multiple requirements and moderate complexity." if priority == "Medium" else ""}{"This task is standard priority with no urgency indicators." if priority == "Low" else ""}

---

## Requires Human Approval? {approval}

{"One or more steps involve external-facing or irreversible actions (sending, publishing, deleting). A human must review and approve the output before it is released." if approval == "Yes" else "All steps are internal and non-destructive. The output can proceed to review without prior approval, but a final human check is still recommended."}

---

## Suggested Output

{suggestion}

---

> **Note:** This is a PLAN only. The task has NOT been executed.
> The next step is for the AI Employee or a human to carry out the steps above.
"""

    # Write the plan
    plan_name = f"Plan_{timestamp_file}_{filename}"
    plan_path = os.path.join(NEEDS_ACTION_DIR, plan_name)

    try:
        os.makedirs(NEEDS_ACTION_DIR, exist_ok=True)
        with open(plan_path, "w", encoding="utf-8") as f:
            f.write(plan_md)
        return plan_path
    except Exception as exc:
        print(f"[ERROR] Cannot write plan: {exc}")
        return None


# ---------------------------------------------------------------------------
# Main — scan Inbox and plan every unprocessed file
# ---------------------------------------------------------------------------
def main():
    os.makedirs(INBOX_DIR, exist_ok=True)
    os.makedirs(NEEDS_ACTION_DIR, exist_ok=True)

    print("=" * 55)
    print("  AI Employee — Reasoning Planner (Bronze Tier)")
    print("=" * 55)

    # Collect existing plan filenames so we don't re-plan already-planned tasks
    existing_plans = set(os.listdir(NEEDS_ACTION_DIR))

    inbox_files = sorted(
        f for f in os.listdir(INBOX_DIR)
        if f.endswith(".md")
    )

    if not inbox_files:
        print("[INFO]  Inbox is empty. Nothing to plan.")
        return

    planned = 0
    skipped = 0

    for filename in inbox_files:
        # Check if a plan already exists for this file (any timestamp)
        already_planned = any(
            p.endswith(f"_{filename}") and p.startswith("Plan_")
            for p in existing_plans
        )
        if already_planned:
            print(f"[SKIP]  Plan already exists for {filename}")
            skipped += 1
            continue

        filepath = os.path.join(INBOX_DIR, filename)
        print(f"\n[READ]  Processing: {filename}")

        result = create_plan(filepath)
        if result:
            plan_basename = os.path.basename(result)
            print(f"[PLAN]  Created: Needs_Action/{plan_basename}")
            planned += 1
        else:
            skipped += 1

    print("\n" + "=" * 55)
    print(f"  Done.  Plans created: {planned}  |  Skipped: {skipped}")
    print("=" * 55)


if __name__ == "__main__":
    main()

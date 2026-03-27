# Skill: Task Planner

**Skill ID:** task-planner
**Tier:** Bronze
**Version:** 1.0.0
**Depends on:** file-triage (runs after triage has accepted the file)

---

## Purpose

You are an AI employee responsible for turning raw tasks into structured execution plans. You read markdown files from `vault/Inbox/`, analyze them, produce a step-by-step plan, and save the plan to `vault/Needs_Action/`.

You do NOT execute tasks. You only plan them. Execution is a separate responsibility handled downstream by a human or another agent.

---

## When This Skill Is Invoked

This skill activates when one or more `.md` files exist in `vault/Inbox/` that do not yet have a corresponding `Plan_*` file in `vault/Needs_Action/`. It processes every such file in FIFO order (oldest first).

---

## Workflow Overview

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  1. Read     │────▶│  2. Analyze  │────▶│  3. Break    │
│     Task     │     │     Intent   │     │  Into Steps  │
└─────────────┘     └─────────────┘     └──────┬──────┘
                                                │
┌─────────────┐     ┌─────────────┐     ┌──────▼──────┐
│  6. Save     │◀────│  5. Check    │◀────│  4. Assign   │
│  Plan.md     │     │  Approval    │     │  Priority    │
└─────────────┘     └─────────────┘     └─────────────┘
```

Each stage is described in full below.

---

## Stage 1 — Read the Task

1. Open the `.md` file from `vault/Inbox/`.
2. Read the entire file content into memory.
3. If the file is empty or unreadable, log the issue and skip it. Do not produce a plan for empty files.
4. Extract the following raw elements before moving to Stage 2:
   - **Title**: The first line starting with `#`. If none, use the filename.
   - **Body**: All non-heading, non-empty lines.
   - **Bullet points**: All lines starting with `- ` (requirements, constraints, details).
   - **Metadata labels**: Lines matching the pattern `Label:` (e.g., `Task:`, `Output format:`).

---

## Stage 2 — Analyze Intent

Determine what the task is asking the agent to do. Classify it into exactly one primary category:

| Category | Trigger Keywords | Example |
|----------|-----------------|---------|
| Content Creation | write, draft, compose, create, generate | "Write a blog post about..." |
| Communication | reply, respond, answer, email, message | "Reply to the client who..." |
| Analysis | summarize, review, analyze, compare, evaluate | "Summarize this report..." |
| Planning | schedule, plan, organize, arrange, coordinate | "Plan the team meeting for..." |
| Maintenance | fix, update, change, correct, revise | "Update the pricing page..." |
| Research | find, search, investigate, look up, explore | "Research competitors in..." |

**Rules:**
- Scan the full text, not just the title.
- If multiple categories match, choose the one whose trigger word appears first in the text.
- If no keywords match, classify as **General** and note this in the plan.
- Record the matched keyword and category — these go into the plan output.

---

## Stage 3 — Break into Steps

Convert the task into an ordered list of concrete, actionable steps.

### 3a — Standard Steps (always included)

Every plan starts and ends with these anchoring steps:

1. **First step (always):** "Read and understand the full task description from Inbox."
2. **Last step (always):** "Write the final output as a markdown file and place it in vault/Needs_Action/ for review."

### 3b — Requirement Steps

Between the anchoring steps, insert steps derived from the file's content:

- For each bullet point found in Stage 1, create a sub-step prefixed with `→`.
- Group them under a parent step: "Address the following requirements:"

### 3c — Category-Specific Steps

Based on the category from Stage 2, insert the appropriate process steps:

**Content Creation:**
- Draft the content following all listed requirements.
- Review the draft for tone, clarity, grammar, and completeness.

**Communication:**
- Draft the response addressing every point raised in the original request.
- Match the tone to the requested style (professional, casual, formal).
- Include a greeting and sign-off appropriate for the medium (email, message, post).

**Analysis:**
- Gather and review all referenced source material.
- Produce a structured summary with key findings, data points, and conclusions.

**Planning:**
- Define the objective, participants, and timeline.
- Outline milestones, dependencies, and deliverables.

**Maintenance:**
- Identify the specific item to update or fix.
- Apply the change and verify the result against the original requirement.

**Research:**
- Define the research question clearly.
- Identify sources, collect findings, and present them in a structured format.

**General:**
- Break the task into logical sub-tasks.
- Complete each sub-task in sequence.

---

## Stage 4 — Assign Priority

Evaluate the task and assign exactly one priority level.

### High Priority

Assign **High** if ANY of these conditions are true:

- The text contains urgency words: `urgent`, `asap`, `immediately`, `critical`, `deadline`, `emergency`, `right away`, `top priority`.
- The text specifies a date or time constraint (e.g., "by Friday", "before 3 PM").
- The text mentions consequences of delay (e.g., "client is waiting", "blocking the release").

### Medium Priority

Assign **Medium** if NONE of the High conditions are met, but ANY of these are true:

- The task has 4 or more bullet-point requirements.
- The task involves multiple deliverables or stages.
- The task references other people, teams, or stakeholders.

### Low Priority

Assign **Low** if NONE of the High or Medium conditions are met. This is the default.

### Priority Justification

Always include a one-sentence explanation of why the priority was assigned. Examples:
- "High — contains the keyword 'urgent' and specifies a Friday deadline."
- "Medium — has 5 requirements indicating moderate complexity."
- "Low — standard task with no urgency indicators or complexity signals."

---

## Stage 5 — Check Human Approval Requirement

Determine whether the plan output requires human sign-off before execution.

### Approval Required (Yes)

Mark as **Yes** if the task involves ANY of these:

- Sending content externally: `send`, `email`, `post`, `publish`, `submit`, `announce`, `release`.
- Financial actions: `payment`, `invoice`, `contract`, `purchase`, `refund`.
- Destructive actions: `delete`, `remove`, `cancel`, `terminate`, `revoke`.
- Access or permissions: `deploy`, `grant`, `share`, `transfer`.

### No Approval Required (No)

Mark as **No** only if the task is entirely internal and non-destructive:
- Drafting content that stays inside the vault.
- Summarizing information.
- Organizing or planning without external impact.

### When in Doubt

Default to **Yes**. False positives (unnecessary approvals) are harmless. False negatives (skipping needed approval) can cause real damage.

### Approval Justification

Always include a one-sentence explanation. Examples:
- "Yes — task involves sending an email to an external client."
- "No — output is an internal draft that stays within the vault."

---

## Stage 6 — Save the Plan

Write the completed plan as a markdown file to `vault/Needs_Action/`.

### File Naming

Format: `Plan_<YYYYMMDD>_<HHMMSS>_<original-filename>.md`

Examples:
- Input: `client-email.md` → Output: `Plan_20260309_143022_client-email.md`
- Input: `weekly-report.md` → Output: `Plan_20260309_143025_weekly-report.md`

### Output Template

Every plan file must use this exact structure:

```markdown
# Task Plan — <Title>

---

| Field                    | Value                              |
|--------------------------|------------------------------------|
| Source file               | `Inbox/<original-filename>`       |
| Plan created              | <YYYY-MM-DD HH:MM:SS>            |
| Category                  | <Category from Stage 2>           |
| Priority                  | <High / Medium / Low>             |
| Requires Human Approval?  | <Yes / No>                        |

---

## Original Task

```
<Full original file content, unmodified>
```

---

## Objective

<First meaningful sentence describing what the task asks for>

---

## Step-by-Step Plan

<Numbered list from Stage 3>

---

## Priority: <Level>

<One-sentence justification from Stage 4>

---

## Requires Human Approval? <Yes/No>

<One-sentence justification from Stage 5>

---

## Suggested Output

<One-sentence description of what the final deliverable should look like>

---

> **Note:** This is a PLAN only. The task has NOT been executed.
> The next step is for the AI Employee or a human to carry out the steps above.
```

---

## Idempotency

This skill is safe to run multiple times. Before creating a plan, check `vault/Needs_Action/` for any file matching `Plan_*_<original-filename>.md`. If a match exists, skip that file. Never overwrite an existing plan.

---

## Boundaries

| Allowed | Not Allowed |
|---------|-------------|
| Read files from `vault/Inbox/` | Modify or delete Inbox files |
| Write plan files to `vault/Needs_Action/` | Write files anywhere else |
| Classify, prioritize, and structure tasks | Execute the tasks themselves |
| Flag tasks for human approval | Approve tasks on behalf of humans |
| Assign priority levels | Override a human-assigned priority |

---

## Error Handling

| Situation | Action |
|-----------|--------|
| File is empty | Skip it, log `[SKIP] <filename> is empty` |
| File cannot be read (encoding/permission) | Skip it, log `[ERROR] Cannot read <filename>` |
| No intent detected | Set category to "General", note "Unclear — needs human review" in the plan |
| Plan file already exists for this task | Skip it, log `[SKIP] Plan already exists for <filename>` |
| `Needs_Action/` folder missing | Create it, then write the plan |

---

## Relationship to Other Skills

```
file-triage (upstream)
    │
    ▼
task-planner (this skill)
    │
    ▼
[execution agent or human] (downstream)
```

- **file-triage** decides whether a file needs action or is already done.
- **task-planner** takes files that need action and creates execution plans.
- The **execution agent** (or a human) reads the plan and carries out the work.

This skill sits in the middle of the pipeline. It consumes triage output and produces plans for execution.

# ?? Task Plan — Urgent Monthly Report

---

| Field                    | Value                                |
|--------------------------|--------------------------------------|
| Source file               | `Inbox/urgent-report.md`                  |
| Plan created              | 2026-03-11 15:45:14                         |
| Category                  | Sales                        |
| Urgency                   | **CRITICAL**                    |
| Planner Decision          | **AWAITING MANAGER APPROVAL**                  |
| Assigned Agent            | `task_agent`                |
| Confidence                | 55% [=====-----]     |
| Risk Score                | 15% [=---------]     |
| Queue Status              | `pending`                   |
| Max Retries               | 3                |

---

## Planner Reasoning

> Category: Keyword match: revenue, report | Priority: Keyword analysis: detected urgent, risk:send

---

## Extracted Metadata

| Field            | Value                                    |
|------------------|------------------------------------------|
| Sender           | Not specified              |
| Deadline         | Today (end of day)               |
| Description      | Summarize the monthly sales data and send it to the management team by end of day.        |
| Required Action  | Summarize the monthly sales data and send it to the management team by end of day.    |
| Sub-Category     | Sales                     |
| Parse Method     | `local`                   |
| Urgency Signals  | urgent, risk:send |
| Keywords         | revenue, report |

---

## Original Task

```markdown
# Urgent Monthly Report

Summarize the monthly sales data and send it to the management team by end of day.

Requirements:
- Include total revenue numbers
- Compare with last month
- Highlight top performing products
- Add recommendations for next month
- Keep it under 2 pages
```

---

## Step-by-Step Plan

1. Read and understand the full task description.
2. Identify key requirements:
3.   -> Include total revenue numbers
4.   -> Compare with last month
5.   -> Highlight top performing products
6.   -> Add recommendations for next month
7.   -> Keep it under 2 pages
8. Review the sales context and client history.
9. Draft the proposal or outreach material.
10. Include relevant metrics and value propositions.
11. Write the final output as a markdown file.
12. Route the output for review or approval.

---

## Urgency: CRITICAL

CRITICAL — Multiple urgency indicators. Execute immediately.

---

## Planner Action: AWAITING MANAGER APPROVAL

Task involves external-facing or irreversible actions (risk score > 40%). An approval request has been placed in Needs_Approval/. Execution is paused until a manager responds with APPROVED or REJECTED.

---

## Output Format

```json
{
  "task": "Urgent Monthly Report",
  "priority": "critical",
  "action": "ask_manager",
  "requires_approval": true,
  "status": "pending",
  "assigned_agent": "task_agent",
  "retries": 0
}
```

---

> **Gold Tier — Autonomous Planner**
> Decision: `ask_manager` | Agent: `task_agent` | Queue: `pending`

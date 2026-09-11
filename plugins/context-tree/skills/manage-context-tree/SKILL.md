---
name: manage-context-tree
description: Maintain compact topic trees, configurable batch consolidation, topic switching, and state handoffs across long-running Codex tasks. Use when starting or continuing a task, changing topics, opening topic settings, creating a topic, reaching a context threshold, or consolidating pending turns into durable goals, decisions, results, issues, artifacts, constraints, and next actions.
---

# Manage Context Tree

Use `./scripts/context_tree.py`. Hooks capture every completed turn locally without another model call. When the global interval is reached, queue one background AI batch and start it only after the active request finishes.

## Route Topics

Default to automatic routing. Do not ask the user to manage topics routinely.

On every prompt:

1. Follow an explicit topic ID or title.
2. In sticky mode, keep the selected topic until the user switches.
3. In automatic mode, attach the strongest semantic match when it is clear.
4. Ask one short question only when two or more topics are genuinely ambiguous.
5. Create a topic only for a genuinely new broad direction. Put narrower work in a branch or node, and leave uncertain turns in the shared pending inbox until batch classification.

Do not prefer a topic merely because it was used recently. Completed topics remain selectable. Candidate context is read-only until attached.

Use:

```text
python ./scripts/context_tree.py match --text "<request>"
python ./scripts/context_tree.py attach --session <SESSION_ID> --topic <TOPIC_ID>
python ./scripts/context_tree.py topic-create --session <SESSION_ID> --title "<title>" --summary "<goal>"
```

## Topic Settings

Topic switching is a local UI operation and should not consume an AI turn. Prefer the marketplace launcher `open-context-tree-settings.cmd` or run:

```text
python ./scripts/context_tree.py ui
```

When the MCP Apps UI is available, call `render_context_tree` to show the compact collapsed button. Once rendered, its topic switching and graph actions call local MCP tools directly. Use `context_tree_open_graph` to open a topic's full interactive graph in the default browser.

The panel contains:

- Automatic routing, recommended;
- every saved topic, sorted by title rather than recency;
- Create a new topic;
- consolidation interval: 1, 2, 3, 5, or a custom value from 1 to 50.

CLI equivalents are available for automation:

```text
python ./scripts/context_tree.py settings
python ./scripts/context_tree.py routing-set --mode auto --every 3
python ./scripts/context_tree.py routing-set --mode sticky --topic <TOPIC_ID> --every 5
```

Sticky mode persists across fresh tasks until changed. Automatic mode may reassign pending turns during batch review.

## Capture Versus Consolidate

Treat saving and AI organization as different operations:

- Rollout observer: each user prompt reserves one pending turn immediately while it is active; later assistant output updates that same record. If the request aborts with no assistant output or execution trace, remove the reservation. Preserve aborted requests that contain partial output.
- `Stop` Hook: when available, finalizes the bounded provisional capsule and exact values locally. It does not call another model.
- Pending tail: incomplete batches remain durable and are appended to a fresh task's Active Frontier.
- Consolidation: after N completed pending turns globally across all sessions, queue one background AI job. The currently generating turn is excluded; the worker starts after active requests finish so it does not compete with first-token delivery.

Therefore, leaving after four turns with an interval of five does not lose those turns. A later task sees the formal snapshot plus all four pending capsules.

The background worker classifies the queued batch with a strict JSON schema, routes every Turn independently, and writes one consolidation payload per affected topic:

```json
{
  "topic_id": "TOPIC_ID",
  "summary": "What changed across this batch",
  "turn_ids": ["TURN_ID"],
  "nodes": [
    {
      "id": "batch-alias",
      "type": "decision",
      "label": "Short graph label",
      "capsule": "Enough detail to reproduce the decision",
      "exact_data": {}
    }
  ],
  "invalidate": [
    {"node_id": "OLD_NODE", "status": "superseded", "reason": "Why it changed"}
  ],
  "edges": [
    {"from": "batch-alias", "to": "OTHER_NODE", "relation": "depends_on"}
  ]
}
```

Manual recovery can still run:

```text
python ./scripts/context_tree.py consolidate --input <payload.json>
```

Allowed node types are `goal`, `constraint`, `decision`, `attempt`, `result`, `issue`, `artifact`, `question`, `fact`, and `milestone`.

Preserve exact paths, URLs, IDs, versions, numerical results, artifact fingerprints, and error text. Discard ordinary explanation, repeated reasoning, dead-end narration, and conversational filler. A failed AI request leaves every Turn pending and records the terminal error for a later retry.

Every graph node has two layers: a short label for scanning and a detailed capsule plus structured exact data behind it. Derive subtopics from the actual Turn content and reuse semantically equivalent branches; never impose a predefined branch directory. Ordinary Turns should create at most four nodes. A Goal-mode Turn, a Turn lasting at least one hour, or an unusually large Turn is independently due even before the global interval and should be decomposed into four to ten chronological nodes. Temporary detailed source text is removed after successful consolidation.

Historical nodes can be independently re-enriched without changing their timeline or deleting exact data:

```text
python ./scripts/context_tree.py handoff-enrich --topic <TOPIC_ID> --batch-size 6
```

The enrichment is node-specific and resumable. It skips nodes that already have a structured handoff unless `--force` is supplied. Each fact belongs in one best field; absent evidence remains empty rather than being copied from another field.

## Fresh Task Recommendation

Do not treat first-token latency as a standalone signal because provider load and network delay can dominate it. Distinguish current-window pressure from long-task degradation:

- Prefer the `model_context_window` recorded by Codex; use the configured capacity only as a fallback.
- Show measured window occupancy and compression-stage history; do not turn avoidable-token projections into visible “another full window” claims.
- Recommend a fresh task when current context reaches two-thirds of the real model window.
- Treat compaction count and rollout size as quality/local-performance warnings, not API-pressure evidence.
- When server history is absent, label money and API latency as pending rather than estimating them.

The original rollout remains on disk, but after the initial pass the monitor reads only bytes appended after its cached file offset.

## Automatic Batch Classification

When a due global batch spans sessions or topics, classify each provisional Turn before consolidation. Keep the topic set broad (for example research papers, project exploration, or side-business research), and use `branch_title` for narrower work:

```json
{
  "assignments": [
    {"turn_id": "TURN_ID", "topic_id": "EXISTING_TOPIC", "branch_title": "Data processing"},
    {"turn_id": "TURN_ID", "new_topic_title": "Project exploration", "new_topic_summary": "Build and evaluate projects", "branch_title": "Context continuity"}
  ]
}
```

Run `route` first, then consolidate the resulting pending groups separately:

```text
python ./scripts/context_tree.py route --input <assignments.json>
python ./scripts/context_tree.py pending --topic <TOPIC_ID>
```

Do not move a Turn merely because another topic is recent. Use its user intent, result, exact values, and relationship to the Active Frontier.

## Retrieve And Hand Off

Load the handoff first, then query individual nodes only when needed:

```text
python ./scripts/context_tree.py handoff --topic <TOPIC_ID> --budget 1200
python ./scripts/context_tree.py query --topic <TOPIC_ID> --text "<needed detail>"
python ./scripts/context_tree.py graph --topic <TOPIC_ID> --format mermaid
python ./scripts/context_tree.py verify
```

The handoff includes the latest consolidated snapshot and the bounded pending tail. Treat invalidated and superseded nodes as history rather than current instructions.

When the local graph stores a one-shot resume pointer, attach only a session created after the pointer was armed; restored sessions that already existed must not claim or clear it. Include the selected frontier node's capsule, exact data, source Turn, and occurrence time in addition to the branch snapshot. No passphrase is required: treat the fresh task's first user prompt as the next instruction, then clear the pointer after injection. If the app does not emit `SessionStart` for a newly opened task, the first `UserPromptSubmit` hook must claim and inject the same pointer before ordinary topic routing. Keep cross-branch dependencies as references; do not copy one node into several branches.

## Historical Session Backfill

The settings panel can import a Codex session ID to repair memory that predates Context Tree. Import creates a chronological background job and starts an ephemeral structured-output AI worker. It does not append the old session as if it happened today.

Classify each historical Turn independently because one session may switch topics. Use `occurred_at` as the authoritative order, reuse broad topics, put narrower work in branches, and inspect the supplied before/after neighbors before choosing node status. Older decisions that a later node already replaced must enter as superseded history.

```text
python ./scripts/context_tree.py backfill-create --session <SESSION_ID>
python ./scripts/context_tree.py backfill-status --job <JOB_ID>
python ./scripts/context_tree.py backfill-apply --input <payload.json>
```

The apply step resequences affected topic Turns by original time and rebuilds `timeline_next` edges, so a late import can appear before or between existing nodes without changing the current state to an older result.

See [schema.md](references/schema.md) for lifecycle rules and relationships.

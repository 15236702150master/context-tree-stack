# Context Tree Schema

## Storage Roles

- `context-tree.db`: transactional working projection.
- `events.jsonl`: append-only recovery events; never contains the full transcript.
- `config.json`: user-level routing and consolidation settings.

The default store is `~/.context-tree`, shared across local tasks. The local settings server binds only to `127.0.0.1`, uses a per-launch request token, and changes routing without placing topic lists or management commands in model context.

Every captured Turn has `consolidation_status=pending` until an AI batch review creates durable graph nodes. Formal snapshots advance only after consolidation. The Active Frontier appends a bounded pending tail at read time, so continuity never depends on reaching the configured batch size.

Turns and nodes are bitemporal:

- `occurred_at`: when the source conversation event happened; controls topic sequence, graph position, timeline neighbors, and current-state projection.
- `created_at`: when Context Tree recorded or imported it; retained for audit and deterministic tie-breaking.

Historical imports use `backfill_jobs` and `backfill_items`. Items remain queued until AI classifies each Turn into a broad topic and optional branch. Applying a batch resequences affected topics and rebuilds `timeline_next` edges without rewriting original timestamps.

## Node Lifecycle

Nodes use `active`, `resolved`, `superseded`, `invalidated`, or `closed`.

- Use `superseded` when a later valid decision replaces an earlier one.
- Use `invalidated` when the earlier fact was wrong.
- Use `resolved` for an issue or question that received an answer.
- Preserve the old node and explain the transition; do not overwrite history.

Recommended edges are `depends_on`, `produced`, `resolved_by`, `supersedes`, `contradicts`, `supports`, `branch_of`, `merged_into`, `next`, and generated `timeline_next`.

## Active Frontier

Build the new-task handoff only from active goals and constraints, current decisions, confirmed results and artifacts, unresolved issues and questions, and next actions. Exclude closed attempts and superseded decisions unless a query explicitly asks for their history.

## Topic Attachment

Treat a new session as `unmatched` unless sticky routing selects a topic. Candidate topics remain read-only until attached. Explicit topic IDs and names win over similarity. Automatic routing uses semantic fit without recency weighting; ambiguous matches show no more than three candidates. Topic settings always offer automatic routing, every saved topic, and a new topic.

## Delta Quality

A turn delta describes intent, response, method, action, outcome, exception, and next step independently. Empty fields are valid when nothing happened in that category. Preserve exact values in structured data rather than embedding them in long prose.

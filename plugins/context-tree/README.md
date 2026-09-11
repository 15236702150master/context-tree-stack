# Context Tree

Context Tree is a local Codex plugin for carrying long-running work into fresh tasks without replaying the original conversation. Every turn is saved locally without another model call; AI consolidation runs only at a configurable batch interval.

## Components

- `skills/manage-context-tree`: semantic rules for attaching, recording, querying, and handing off topics.
- `hooks.json`: lifecycle enforcement for task start, prompt submission, and task stop.
- `scripts/context_tree.py`: dependency-free SQLite store, query CLI, and hook adapter.
- `scripts/context_tree_mcp.py`: MCP Apps server for the compact in-conversation widget.
- `scripts/context_tree_float.py`: auto-starting desktop monitor for providers where MCP Apps UI is unavailable.
- `assets/graph.html`: full interactive graph with pan, zoom, search, and node details.

## Real Session Usage

Open `open-settings.cmd`, enter your usage sidecar URL and API key, then choose the model context capacity and warning thresholds. The endpoint is intentionally blank on first install, so the plugin starts in local-estimate mode. The API key is stored separately in `~/.context-tree/credentials.json`; it is never returned to the browser page or MCP structured output.

When configured, the compact widget reads server-recorded usage from the read-only endpoints and keeps every complete `X-Codex-Window-ID` isolated. Only sessions with a request currently running are shown. Parallel requests appear as separate cards with the live Codex title, topic, context tokens, latest first-token time, recent cost, request count, and the exact number of local `context_compacted` events. User renames are read from Codex's append-only title index. Active cards prefer Codex's local `model_context_window`; the capacity entered in Settings is only a fallback when runtime metadata is absent.

The widget displays user-facing billing signals rather than raw token diagnostics: current-turn total cost, current-turn cache-hit ratio, first-token time, turn duration, current compression-stage total cost, current-stage request count, average stage cost, and average stage cache-hit ratio. Each compression stage starts a fresh bucket at the local `context_compacted` timestamp. The “next compression” line is an experience-based progress hint from previous stage request counts, not a fixed official countdown.

The Sub2API server must expose these API-key authenticated read-only endpoints for real dollar billing:

- `GET /v1/sub2api/usage/sessions`
- `GET /v1/sub2api/usage/sessions/:session_id/requests`
- `GET /v1/sub2api/usage/threads/:thread_id/requests`

Without a configured key, or when the server is temporarily unavailable, the widget automatically displays the locally measured transcript estimate. Hooks never perform a network request, so usage telemetry does not increase first-token latency.

## Try The Store

```powershell
python .\scripts\context_tree.py init
python .\scripts\context_tree.py topic-create --title "Context Tree plugin" --summary "Build and validate the plugin"
python .\scripts\context_tree.py topic-list
```

The default store is `~/.context-tree`, shared by all local tasks. Set `CONTEXT_TREE_HOME` to use another location.

## New Task Behavior

Automatic routing is the default. Open `open-settings.cmd` or run `python .\scripts\context_tree.py ui` to manage topics without sending anything to the AI. The local panel can select a sticky topic, return to automatic routing, create a topic, or choose a 1/2/3/5/custom consolidation interval. Topic matching is semantic rather than recency-based.

The desktop floating button is hidden while Codex is idle by default and appears automatically during an active request. Enable **悬浮按钮常驻** in Settings to keep only its collapsed green button visible while idle; changing this option is local and takes effect within three seconds without restarting or using an AI turn.

In an MCP Apps-compatible Codex surface, ask to show Context Tree once to render the compact button. Opening the panel, searching, switching topics, and changing local settings then use MCP tool calls against SQLite rather than model classification. The panel starts collapsed, expands on click, and automatically collapses after inactivity.

When the MCP server initializes, it idempotently registers the three Context Tree hooks in the current user's `~/.codex/hooks.json`. Existing non-Context-Tree hooks are preserved, and stale Context Tree commands from older plugin cache versions are replaced with the currently installed plugin path. This keeps fresh installs and plugin updates working without manual hook editing.

Use the graph button beside any topic to open its complete graph in the default browser. The graph server binds only to `127.0.0.1`, selects an available port automatically, and uses a per-process request token.

The graph derives subtopics from consolidated turn content rather than a predefined directory. Each node has one primary branch, a short graph label, a detailed capsule, structured exact data, and its source Turn. Branches appear as chronological lanes. Selecting a subtopic highlights all of its nodes and every related cross-branch dependency while fading unrelated work; cross-branch relations are dashed and never participate in layout depth, so cyclic dependencies remain displayable. Select a frontier node and choose **下一新会话继续** to store a one-shot topic/branch/node pointer. The next Codex task receives only that branch snapshot, pending tail, and selected node details, then clears the pointer. Copying the same continuation card remains available as a fallback.

The consolidation interval is global for the user, not per session. User prompts are observed directly from recent Codex rollouts, so active, interrupted, parallel, and hook-less sessions still advance the batch counter exactly once. Assistant results are filled into the same pending record as they arrive. At the threshold, completed Turns are queued for one background AI batch; the currently generating Turn is excluded and the worker starts after active requests finish. The next batch can span several Codex tasks. Automatic classification reuses a small set of broad topics and creates branches for narrower work; unmatched turns wait in one shared inbox instead of creating many tiny topics. Failed AI calls preserve the pending Turns for retry.

The monitor separates completed **Ready** turns from incomplete **Recording** turns and from turns already **AI processing**. Only Ready turns count toward the configured consolidation interval. An interrupted turn remains durable in Recording until an assistant result is observed, so it is never presented as an AI-ready batch item.

Before consolidation, bounded user and assistant text is kept in a temporary detail queue so the background AI can create useful node capsules instead of relying on one-line summaries. The queue entry is deleted after successful consolidation. Ordinary turns produce at most four nodes. Goal-mode turns, turns lasting at least one hour, or unusually large turns bypass the global N-turn minimum and are processed independently into four to ten chronological nodes once no Codex request is active. The hidden monitor continues launching due work while Codex is idle, so a long split does not block the conversation that produced it.

With a configured API Key, active Codex task IDs are matched to the newest server `thread_id` window slot and request details use the complete server `session_id`. If the server is unavailable, Context Tree reads Codex's local `token_count` events and response timestamps, so context size and observed first-token latency do not fall back to zero.

Context pressure and task lifecycle are separate signals. The visible percentage is always **window occupancy** (`current input tokens / runtime model window`). Context Tree no longer turns avoidable-token projections into visible “another full window” warnings. Without server billing data it shows measured local values only. A current context above two-thirds of the real model window remains an immediate fresh-task recommendation; compaction count and rollout size produce a quality/local-performance warning only. First-token latency is diagnostic because network and provider load can dominate it. Rollout monitoring keeps a per-file byte offset in memory and parses only appended JSONL records after the initial scan.

For cost comparison, Context Tree uses the first local `context_compacted` event as the baseline boundary. Requests before it form the permanent **pre-compression** baseline; requests between later compression events form separate stages. The card shows the current turn's total actual cost, cache-hit ratio, API first-token latency, and total/average duration, then compares the current compression stage with the pre-compression average. Expanding the card shows every stage with request count, total cost, average cost, context-only cost, cache-hit ratio, and model/reasoning/service-tier configuration. A signed difference preserves both increases and decreases. All grouping stays within the current Codex thread and API key, so parallel tasks and other users never enter the baseline.

When the server endpoint is unavailable, the same stage panel remains visible using Codex rollout data: API call count, average input tokens, average window occupancy, and cache-hit ratio for every compression stage. Actual money, API first-token latency, and total duration are marked as pending until the server request-history endpoint is deployed.

On Windows, batch consolidation and historical backfill launch both the worker and its nested `codex exec` process without a console window.

## Historical Backfill

Paste a local Codex session ID into **Historical memory backfill** in Settings. Context Tree parses every Turn with its original timestamp, then launches an ephemeral read-only Codex classification worker in configurable batches. A single session may route to several topics and branches. Imported nodes are inserted by original occurrence time, topic sequence numbers are recalculated, and timeline edges are rebuilt; import time remains separate audit metadata. Re-importing an active job reuses it instead of duplicating nodes.

## Data Durability

The SQLite database is the fast projection. `events.jsonl` records committed topic, attachment, turn, and snapshot events without retaining the original transcript.

## Compatibility

- Python 3.10 or newer must be available as `python`. The Unix hook path may use `python3`.
- Paths are resolved from `${PLUGIN_ROOT}` and the current user's home directory; no username or installation path is hard-coded.
- Session-start and prompt hooks launch the desktop monitor with a per-user single-instance lock. It hides when no request is active unless the user enables persistent mode, and reappears for current or parallel requests.
- The widget uses the MCP Apps bridge and feature detection instead of host-name checks.
- Layouts support narrow iframe widths, mobile browsers, light/dark color schemes, keyboard focus, touch panning, and reduced-motion preferences.
- Clients without MCP Apps UI can still use all Context Tree tools and the browser settings page.

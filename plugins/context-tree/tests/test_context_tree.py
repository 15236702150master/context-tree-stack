from __future__ import annotations

import importlib.util
import http.server
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from unittest import mock
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "context_tree.py"
MCP_SCRIPT = ROOT / "scripts" / "context_tree_mcp.py"
FLOAT_SCRIPT = ROOT / "scripts" / "context_tree_float.py"
SPEC = importlib.util.spec_from_file_location("context_tree", SCRIPT)
ct = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(ct)
sys.modules["context_tree"] = ct
MCP_SPEC = importlib.util.spec_from_file_location("context_tree_mcp", MCP_SCRIPT)
context_tree_mcp = importlib.util.module_from_spec(MCP_SPEC)
assert MCP_SPEC.loader
MCP_SPEC.loader.exec_module(context_tree_mcp)
FLOAT_SPEC = importlib.util.spec_from_file_location("context_tree_float", FLOAT_SCRIPT)
context_tree_float = importlib.util.module_from_spec(FLOAT_SPEC)
assert FLOAT_SPEC.loader
FLOAT_SPEC.loader.exec_module(context_tree_float)


class ContextTreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = Path(self.temp.name) / ".context-tree"
        self.db = ct.initialize_store(self.store)
        ct.save_config(self.store, {"float_enabled": False, "batch_ai_auto_start": False})

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    def test_plugin_manifest_keeps_hooks_file_with_valid_manifest(self) -> None:
        manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8-sig"))
        self.assertNotIn("hooks", manifest)
        self.assertTrue((ROOT / "hooks.json").is_file())

    def test_mcp_initialize_can_register_user_hooks_idempotently(self) -> None:
        path = Path(self.temp.name) / "hooks.json"
        path.write_text(json.dumps({
            "hooks": {
                "UserPromptSubmit": [
                    {"hooks": [{"type": "command", "command": "old python context_tree.py hook prompt"}]},
                    {"hooks": [{"type": "command", "command": "keep-me"}]},
                ]
            }
        }), encoding="utf-8")
        self.assertTrue(context_tree_mcp.ensure_user_hooks(path))
        self.assertTrue(context_tree_mcp.ensure_user_hooks(path))
        data = json.loads(path.read_text(encoding="utf-8"))
        prompt_hooks = data["hooks"]["UserPromptSubmit"]
        commands = [
            hook["command"] for entry in prompt_hooks for hook in entry.get("hooks", [])
        ]
        self.assertEqual(commands.count("keep-me"), 1)
        self.assertEqual(sum("context_tree.py" in command for command in commands), 1)
        self.assertIn("SessionStart", data["hooks"])
        self.assertIn("Stop", data["hooks"])

    def test_graph_skips_a_redundant_single_same_name_workstream(self) -> None:
        graph = (ROOT / "assets" / "graph.html").read_text(encoding="utf-8")
        self.assertIn("function isRedundantWorkstream", graph)
        self.assertIn("const hideRedundantWorkstream = isRedundantWorkstream", graph)
        self.assertIn("const streams = hideRedundantWorkstream ? []", graph)

    def test_graph_uses_tree_layout_and_routes_dependencies_outside_nodes(self) -> None:
        graph = (ROOT / "assets" / "graph.html").read_text(encoding="utf-8")
        self.assertIn("function calculateLayout(nodes, timelineEdges)", graph)
        self.assertIn("const primaryParent = new Map()", graph)
        self.assertIn("const laneX = fromIsLeft", graph)
        self.assertIn('dependency ? "dependency"', graph)
        self.assertIn("level * 110", graph)

    def test_float_visibility_defaults_hidden_and_supports_persistent_mode(self) -> None:
        self.assertFalse(context_tree_float.should_show_float({"config": {}, "active_sessions": []}))
        self.assertTrue(context_tree_float.should_show_float({
            "config": {"float_persistent": True}, "active_sessions": [],
        }))
        self.assertTrue(context_tree_float.should_show_float({
            "config": {"float_persistent": False}, "active_sessions": [{"session_id": "active"}],
        }))
        self.assertEqual(context_tree_float.ContextTreeFloat._status_color(None, []), context_tree_float.GREEN)
        self.assertEqual(context_tree_float.format_seconds(3823), "3.82 秒")
        self.assertEqual(context_tree_float.format_seconds(24324), "24.3 秒")

    def test_float_detects_codex_desktop_from_windows_process_listing(self) -> None:
        self.assertTrue(context_tree_float._official_codex_path(
            r"C:\Program Files\WindowsApps\OpenAI.Codex_26.803.5235.0_x64__2p2nqsd0c76g0\app\resources\codex.exe"
        ))
        self.assertTrue(context_tree_float._official_codex_path(
            r"D:\Program Files\OpenAI.Codex_26.707.3748.0_x64\app\resources\codex.exe"
        ))
        self.assertFalse(context_tree_float._official_codex_path(r"C:\Tools\codex.exe"))

    def test_commit_snapshot_match_and_duplicate(self) -> None:
        topic_id, branch_id = ct.create_topic(
            self.db, self.store, "Context Tree plugin", "Build resumable topic memory"
        )
        ct.attach_session(self.db, self.store, "session-a", topic_id)
        delta = {
            "event_key": "turn-one",
            "session_id": "session-a",
            "user_intent": "Build a context tree plugin",
            "response_summary": "Implemented the local SQLite store",
            "method_summary": "Use SQLite and JSONL",
            "action_summary": "Created the store and hooks",
            "outcome_summary": "The store initializes successfully",
            "next_step": "Validate fresh-task handoff",
            "nodes": [
                {"id": "decision-store", "type": "decision", "label": "Use SQLite and JSONL", "capsule": "SQLite is the projection and JSONL is the recovery log."},
                {"id": "result-init", "type": "result", "label": "Store initializes", "capsule": "Initialization produced a valid SQLite database."},
            ],
            "edges": [
                {"from": "decision-store", "to": "result-init", "relation": "produced"}
            ],
        }
        created = ct.commit_turn(self.db, self.store, delta)
        duplicate = ct.commit_turn(self.db, self.store, delta)
        self.assertEqual(created["status"], "created")
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM turns").fetchone()[0], 1
        )
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM edges").fetchone()[0], 1
        )
        snapshot = ct.latest_snapshot(self.db, topic_id)
        self.assertEqual(snapshot["current_state"], "The store initializes successfully")
        self.assertIn("Validate fresh-task handoff", snapshot["next_actions"])
        matches = ct.topic_matches(
            self.db, "continue context tree handoff", ct.project_key(self.store)
        )
        self.assertEqual(matches[0]["topic_id"], topic_id)
        handoff = ct.render_handoff(snapshot, budget=1200)
        self.assertIn("Active", handoff.replace("Confirmed results", "Active"))
        self.assertIn("Validate fresh-task handoff", handoff)

    def test_invalidation_removes_node_from_active_frontier(self) -> None:
        topic_id, _ = ct.create_topic(self.db, self.store, "Compression", "Test decisions")
        ct.attach_session(self.db, self.store, "session-b", topic_id)
        first = ct.commit_turn(self.db, self.store, {
            "session_id": "session-b",
            "user_intent": "Choose a store",
            "response_summary": "Choose flat JSON",
            "nodes": [{"id": "old-decision", "type": "decision", "label": "Use flat JSON", "capsule": "Initial choice"}],
        })
        branch_id = first["branch_id"]
        ct.commit_turn(self.db, self.store, {
            "session_id": "session-b",
            "user_intent": "Replace the store",
            "response_summary": "Use SQLite",
            "nodes": [{"type": "decision", "label": "Use SQLite", "capsule": "Replacement choice"}],
            "invalidate": [{"node_id": "old-decision", "status": "superseded", "reason": "Needs transactions"}],
        })
        snapshot = ct.snapshot_projection(self.db, topic_id, branch_id)
        self.assertNotIn("Use flat JSON", snapshot["active_decisions"])
        self.assertIn("Use SQLite", snapshot["active_decisions"])

    def test_stop_hook_is_idempotent_without_transcript_retention(self) -> None:
        project = Path(self.temp.name) / "project"
        project.mkdir()
        transcript = Path(self.temp.name) / "thread.jsonl"
        messages = [
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"text": "Design a durable memory tree"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"text": "Use a compact active frontier and node capsules."}]}},
        ]
        transcript.write_text("\n".join(json.dumps(item) for item in messages), encoding="utf-8")
        payload = json.dumps({
            "hook_event_name": "Stop", "cwd": str(project),
            "session_id": "hook-session", "transcript_path": str(transcript),
            "context_tree_home": str(project / ".context-tree"),
        })
        for _ in range(2):
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "hook", "stop"], input=payload,
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        hook_db = sqlite3.connect(project / ".context-tree" / "context-tree.db")
        self.assertEqual(hook_db.execute("SELECT COUNT(*) FROM turns").fetchone()[0], 1)
        self.assertEqual(hook_db.execute("SELECT COUNT(*) FROM topics").fetchone()[0], 1)
        self.assertEqual(
            hook_db.execute("SELECT consolidation_status FROM turns").fetchone()[0], "pending"
        )
        hook_db.close()
        transcript.unlink()
        self.assertTrue((project / ".context-tree" / "context-tree.db").exists())
        self.assertTrue((project / ".context-tree" / "events.jsonl").exists())

    def test_fresh_task_receives_topic_candidate_and_next_step(self) -> None:
        topic_id, _ = ct.create_topic(
            self.db, self.store, "Context Tree plugin", "Build resumable topic memory"
        )
        ct.attach_session(self.db, self.store, "old-session", topic_id)
        ct.commit_turn(self.db, self.store, {
            "session_id": "old-session",
            "user_intent": "Implement Context Tree topic handoff",
            "response_summary": "The store and hooks are ready",
            "outcome_summary": "A local marketplace installs successfully",
            "next_step": "Test the first prompt in a fresh task",
            "nodes": [{"type": "result", "label": "Marketplace installs", "capsule": "The isolated install completed."}],
        })
        payload = json.dumps({
            "hook_event_name": "UserPromptSubmit",
            "cwd": self.temp.name,
            "context_tree_home": str(self.store),
            "session_id": "fresh-session",
            "prompt": "Continue the Context Tree plugin handoff work",
        })
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "hook", "prompt"], input=payload,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn(topic_id, context)
        self.assertIn("Test the first prompt in a fresh task", context)

    def test_pending_tail_survives_new_session_until_batch_consolidation(self) -> None:
        topic_id, _ = ct.create_topic(self.db, self.store, "Long task", "Continue across sessions")
        ct.attach_session(self.db, self.store, "session-one", topic_id)
        for index in range(1, 3):
            ct.commit_turn(self.db, self.store, {
                "event_key": f"pending-{index}",
                "session_id": "session-one",
                "user_intent": f"Do step {index}",
                "response_summary": f"Completed step {index}",
                "outcome_summary": f"Result {index}",
                "consolidation_status": "pending",
            })
        self.assertEqual(ct.latest_snapshot(self.db, topic_id), {})
        first_handoff = ct.active_context(self.db, topic_id, pending_limit=5)
        self.assertEqual(first_handoff["pending_count"], 2)
        self.assertEqual([item["topic_sequence"] for item in first_handoff["pending_tail"]], [1, 2])

        ct.attach_session(self.db, self.store, "session-two", topic_id)
        ct.commit_turn(self.db, self.store, {
            "event_key": "pending-3",
            "session_id": "session-two",
            "user_intent": "Do step 3",
            "response_summary": "Completed step 3",
            "outcome_summary": "Result 3",
            "consolidation_status": "pending",
        })
        pending = ct.pending_turns(self.db, topic_id)
        self.assertEqual(len(pending), 3)
        result = ct.consolidate_pending(self.db, self.store, {
            "topic_id": topic_id,
            "turn_ids": [item["turn_id"] for item in pending],
            "summary": "Completed the first three steps",
            "nodes": [{"type": "result", "label": "Three steps complete", "capsule": "Steps 1-3 produced verified results."}],
        })
        self.assertEqual(result["status"], "consolidated")
        self.assertEqual(ct.active_context(self.db, topic_id)["pending_count"], 0)
        self.assertIn("Three steps complete", result["snapshot"]["confirmed_results"])

    def test_topic_settings_sticky_mode_and_custom_interval(self) -> None:
        topic_a, _ = ct.create_topic(self.db, self.store, "Alpha", "First topic")
        topic_b, _ = ct.create_topic(self.db, self.store, "Beta", "Second topic")
        config = ct.save_config(self.store, {
            "routing_mode": "sticky", "sticky_topic_id": topic_b,
            "route_every": 5, "consolidate_every": 5,
        })
        text = ct.all_topics_text(self.db, config)
        self.assertIn(topic_a, text)
        self.assertIn(f"{topic_b}, active) [selected]", text)
        self.assertIn("every 5 turns", text)

    def test_semantic_match_beats_unrelated_recent_topic(self) -> None:
        relevant, _ = ct.create_topic(self.db, self.store, "Earthquake waveform study", "Analyze seismic station waveforms")
        unrelated, _ = ct.create_topic(self.db, self.store, "Shopping list", "Buy fruit and coffee")
        self.db.execute(
            "UPDATE topics SET last_active_at='2099-01-01T00:00:00+00:00' WHERE id=?", (unrelated,)
        )
        self.db.commit()
        matches = ct.topic_matches(
            self.db, "continue seismic waveform analysis", ct.project_key(self.store), 3
        )
        self.assertEqual(matches[0]["topic_id"], relevant)

    def test_batch_router_can_move_pending_turn_to_another_topic(self) -> None:
        source, _ = ct.create_topic(self.db, self.store, "General inbox", "Temporary automatic route")
        target, _ = ct.create_topic(self.db, self.store, "Database design", "Schema and migrations")
        ct.attach_session(self.db, self.store, "route-session", source)
        turn = ct.commit_turn(self.db, self.store, {
            "event_key": "route-me",
            "session_id": "route-session",
            "user_intent": "Design the database migration",
            "response_summary": "Use a versioned schema",
            "consolidation_status": "pending",
        })
        routed = ct.route_pending(self.db, self.store, [{
            "turn_id": turn["turn_id"], "topic_id": target,
        }])
        self.assertEqual(routed["moved"][0]["to_topic_id"], target)
        self.assertEqual(len(ct.pending_turns(self.db, source)), 0)
        self.assertEqual(len(ct.pending_turns(self.db, target)), 1)

    def test_batch_router_recovers_model_placeholder_topic_id(self) -> None:
        source, _ = ct.create_topic(self.db, self.store, "待归类", "Temporary inbox")
        target, _ = ct.create_topic(self.db, self.store, "Database design", "Schema and migrations")
        ct.attach_session(self.db, self.store, "placeholder-session", source)
        turn = ct.commit_turn(self.db, self.store, {
            "event_key": "placeholder-route", "session_id": "placeholder-session",
            "user_intent": "Design a database schema and migration",
            "response_summary": "Use versioned database migrations",
            "consolidation_status": "pending",
        })
        routed = ct.route_pending(self.db, self.store, [{
            "turn_id": turn["turn_id"], "topic_id": "topic_new_database_design",
        }])
        self.assertEqual(routed["moved"][0]["to_topic_id"], target)

    def test_global_pending_batch_spans_sessions_and_topics(self) -> None:
        first, _ = ct.create_topic(self.db, self.store, "Research", "Research work")
        second, _ = ct.create_topic(self.db, self.store, "Projects", "Project work")
        for session_id, topic_id in (("session-a", first), ("session-b", second)):
            ct.attach_session(self.db, self.store, session_id, topic_id)
            ct.commit_turn(self.db, self.store, {
                "event_key": f"pending-{session_id}",
                "session_id": session_id,
                "user_intent": f"Work from {session_id}",
                "response_summary": "Completed one step",
                "consolidation_status": "pending",
            })
        batch = ct.global_pending_turns(self.db, 2)
        self.assertEqual(len(batch), 2)
        self.assertEqual({item["session_id"] for item in batch}, {"session-a", "session-b"})
        self.assertEqual({item["topic_id"] for item in batch}, {first, second})

    def test_rollout_runtime_state_detects_active_and_compactions(self) -> None:
        rollout = Path(self.temp.name) / "rollout.jsonl"
        rollout.write_text("\n".join(json.dumps(item) for item in [
            {"timestamp": "2025-12-31T23:00:00Z", "type": "event_msg", "payload": {"type": "context_compacted"}},
            {"type": "event_msg", "payload": {"type": "task_complete"}},
            {"timestamp": "2026-01-01T00:00:00Z", "type": "event_msg", "payload": {"type": "user_message"}},
            {"type": "turn_context", "payload": {"model": "gpt-test", "effort": "high"}},
            {"timestamp": "2026-01-01T00:00:02.500Z", "type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"text": "Started"}]}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": {"input_tokens": 42000, "cached_input_tokens": 40000}, "model_context_window": 272000}}},
            {"timestamp": "2026-01-01T00:00:03Z", "type": "event_msg", "payload": {"type": "context_compacted"}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": {"input_tokens": 12000, "cached_input_tokens": 10000}, "model_context_window": 272000}}},
        ]), encoding="utf-8")
        state = ct.rollout_runtime_state(rollout)
        self.assertTrue(state["active"])
        self.assertEqual(state["compaction_count"], 2)
        self.assertEqual(state["context_tokens"], 12000)
        self.assertEqual(state["context_window_tokens"], 272000)
        self.assertEqual(state["window_baseline_tokens"], 12000)
        self.assertEqual(state["first_token_ms"], 2500)
        self.assertEqual(state["model"], "gpt-test")
        self.assertEqual(state["reasoning_effort"], "high")
        self.assertEqual(state["turn_request_count"], 2)
        self.assertEqual(state["turn_avg_context_tokens"], 27000)
        self.assertEqual(state["turn_cache_hit_percent"], 92.6)
        self.assertEqual(len(state["compaction_timestamps"]), 2)
        self.assertEqual(len(state["local_epoch_summaries"]), 3)
        self.assertEqual([item["request_count"] for item in state["local_epoch_summaries"]], [0, 1, 1])
        self.assertEqual(state["local_epoch_summaries"][1]["avg_context_tokens"], 42000)
        self.assertEqual(state["local_epoch_summaries"][2]["cache_hit_percent"], 83.3)
        self.assertEqual(state["scanned_bytes"], rollout.stat().st_size)
        unchanged = ct.rollout_runtime_state(rollout)
        self.assertEqual(unchanged["scanned_bytes"], 0)
        self.assertEqual(unchanged["context_tokens"], 12000)
        self.assertEqual(unchanged["compaction_count"], 2)
        previous_size = rollout.stat().st_size
        with rollout.open("a", encoding="utf-8") as handle:
            handle.write('\n{"type":"event_msg","payload":{"type":"task_complete"}}')
        finished = ct.rollout_runtime_state(rollout)
        self.assertFalse(finished["active"])
        self.assertLess(finished["scanned_bytes"], previous_size)

    def test_session_lifecycle_uses_compactions_and_file_size_not_latency(self) -> None:
        config = ct.load_config(self.store)
        normal = ct.session_lifecycle_status(50000, 353400, 20000, 2, 8 * 1024 * 1024, config)
        warning = ct.session_lifecycle_status(100000, 353400, 20000, 2, 8 * 1024 * 1024, config)
        critical = ct.session_lifecycle_status(150000, 353400, 20000, 2, 8 * 1024 * 1024, config)
        hard = ct.session_lifecycle_status(250000, 353400, 240000, 1, 8 * 1024 * 1024, config)
        long_history = ct.session_lifecycle_status(50000, 353400, 45000, 5, 32 * 1024 * 1024, config)
        self.assertEqual(normal["session_level"], "normal")
        self.assertEqual(warning["attention_kind"], "normal")
        self.assertFalse(critical["new_session_recommended"])
        self.assertTrue(hard["new_session_recommended"])
        self.assertEqual(long_history["attention_kind"], "local_history")
        self.assertFalse(long_history["new_session_recommended"])

    def test_windows_background_codex_run_has_no_console_window(self) -> None:
        with mock.patch.object(ct.os, "name", "nt"):
            options = ct.no_window_run_kwargs()
        self.assertEqual(options["creationflags"], subprocess.CREATE_NO_WINDOW)

    def test_ai_output_schemas_require_every_declared_property(self) -> None:
        def assert_strict(value: object) -> None:
            if isinstance(value, dict):
                if value.get("type") == "object" and value.get("additionalProperties") is False:
                    self.assertEqual(
                        set((value.get("properties") or {}).keys()), set(value.get("required") or []),
                    )
                for child in value.values():
                    assert_strict(child)
            elif isinstance(value, list):
                for child in value:
                    assert_strict(child)

        assert_strict(ct.batch_output_schema())
        assert_strict(ct.backfill_output_schema())
        node_schema = ct.batch_output_schema()["properties"]["items"]["items"]["properties"]["nodes"]["items"]
        self.assertEqual(set(node_schema["properties"]["handoff"]["required"]), {
            "objective", "current_state", "method", "rationale", "results",
            "failed_attempts", "environment_resources", "next_actions",
        })

    def test_graph_projection_merges_ordinary_turn_nodes_without_losing_sources(self) -> None:
        nodes = [
            {"id": "goal", "created_turn_id": "turn-a", "type": "goal", "label": "Goal", "capsule": "Goal detail", "status": "active", "occurred_at": "2026-01-01", "branch_id": "branch-a"},
            {"id": "result", "created_turn_id": "turn-a", "type": "result", "label": "Result", "capsule": "Result detail", "status": "active", "occurred_at": "2026-01-01", "branch_id": "branch-a"},
        ]
        projected, edges = ct.graph_display_projection(nodes, [
            {"from_node_id": "goal", "to_node_id": "result", "relation": "produced"},
        ], [{"id": "turn-a", "outcome_summary": "Finished work", "detail_priority": "normal"}])
        self.assertEqual(len(projected), 1)
        self.assertTrue(projected[0]["is_episode"])
        self.assertEqual(set(projected[0]["child_node_ids"]), {"goal", "result"})
        self.assertEqual(projected[0]["resume_node_id"], "result")
        self.assertEqual(edges, [])

    def test_context_tree_branches_become_broad_internal_workstreams(self) -> None:
        subtopic = {"id": "subtopic-context", "title": "Context Tree", "branch_ids": ["a", "b", "c", "d"]}
        branches = [
            {"id": "a", "title": "Context Tree 后台整理优化", "node_count": 3},
            {"id": "b", "title": "压缩阶段计费与响应基准", "node_count": 4},
            {"id": "c", "title": "Context Tree 悬浮入口显示模式", "node_count": 2},
            {"id": "d", "title": "子主题结构与续接指针设计", "node_count": 5},
        ]
        streams = ct.graph_workstreams(subtopic, branches)
        self.assertEqual({item["title"] for item in streams}, {
            "后台整理与路由", "会话用量与换新", "悬浮窗与交互", "知识图谱与续接",
        })

    def test_batch_router_reuses_broad_topic_and_creates_branch(self) -> None:
        inbox, _ = ct.create_topic(self.db, self.store, "待归类", "Pending inbox")
        ct.attach_session(self.db, self.store, "branch-session", inbox)
        turns = []
        for index in range(2):
            turns.append(ct.commit_turn(self.db, self.store, {
                "event_key": f"branch-{index}",
                "session_id": "branch-session",
                "user_intent": "Continue context memory project",
                "response_summary": "Made progress",
                "consolidation_status": "pending",
            })["turn_id"])
        routed = ct.route_pending(self.db, self.store, [
            {"turn_id": turns[0], "topic_id": "topic_model_placeholder", "new_topic_title": "项目探索", "branch_title": "上下文延续"},
            {"turn_id": turns[1], "new_topic_title": "项目探索", "branch_title": "上下文延续"},
        ])
        topic_ids = {item["to_topic_id"] for item in routed["moved"]}
        branch_ids = {item["to_branch_id"] for item in routed["moved"]}
        self.assertEqual(len(topic_ids), 1)
        self.assertEqual(len(branch_ids), 1)
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM topics WHERE title='项目探索'").fetchone()[0], 1
        )

    def test_rollout_prompt_sync_discards_empty_aborted_and_keeps_active_turn_once(self) -> None:
        rollout = Path(self.temp.name) / "sync-rollout.jsonl"
        events = [
            {"type": "event_msg", "payload": {"type": "user_message", "client_id": "old", "message": "Old request"}},
            {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "old-turn"}},
            {"type": "event_msg", "payload": {"type": "user_message", "client_id": "aborted", "message": "Interrupted request"}},
            {"type": "event_msg", "payload": {"type": "turn_aborted"}},
            {"type": "event_msg", "payload": {"type": "user_message", "client_id": "active", "message": "Current request"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"text": "Working now"}]}},
        ]
        rollout.write_text("\n".join(json.dumps(item) for item in events), encoding="utf-8")
        first = ct.sync_rollout_prompts(self.db, self.store, "sync-session", rollout)
        second = ct.sync_rollout_prompts(self.db, self.store, "sync-session", rollout)
        self.assertEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)
        rows = self.db.execute(
            "SELECT user_intent,response_summary FROM turns WHERE session_id=? ORDER BY sequence_no",
            ("sync-session",),
        ).fetchall()
        self.assertEqual([row[0] for row in rows], ["Current request"])
        self.assertEqual(rows[-1][1], "Working now")

    def test_rollout_sync_removes_reserved_turn_when_it_aborts_without_output(self) -> None:
        rollout = Path(self.temp.name) / "reserved-abort.jsonl"
        user = {
            "timestamp": "2026-01-01T00:00:00Z", "type": "event_msg",
            "payload": {"type": "user_message", "client_id": "reserved", "message": "Start work"},
        }
        rollout.write_text(json.dumps(user), encoding="utf-8")
        initial = ct.sync_rollout_prompts(self.db, self.store, "abort-session", rollout)
        self.assertEqual(initial["created"], 1)
        turn = self.db.execute(
            "SELECT id,topic_id,branch_id FROM turns WHERE session_id='abort-session'"
        ).fetchone()
        self.db.execute(
            "INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("abort-node", turn["topic_id"], turn["branch_id"], "milestone", "Reserved", "Reserved",
             "{}", "active", 1.0, turn["id"], turn["id"], None, None, None,
             ct.now(), ct.now(), ct.now()),
        )
        self.db.execute(
            "INSERT INTO edges VALUES(?,?,?,?,?,?,?)",
            ("abort-edge", turn["topic_id"], "abort-node", "abort-node", "timeline_next", turn["id"], ct.now()),
        )
        self.db.commit()
        with rollout.open("a", encoding="utf-8") as handle:
            handle.write("\n" + json.dumps({
                "timestamp": "2026-01-01T00:00:01Z", "type": "event_msg",
                "payload": {"type": "turn_aborted"},
            }))
        final = ct.sync_rollout_prompts(self.db, self.store, "abort-session", rollout)
        self.assertEqual(final["created"], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM turns").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM edges").fetchone()[0], 0)
        metrics = self.db.execute(
            "SELECT turn_count FROM session_metrics WHERE session_id='abort-session'"
        ).fetchone()
        self.assertEqual(metrics[0], 0)

    def test_rollout_sync_keeps_aborted_turn_with_partial_output(self) -> None:
        rollout = Path(self.temp.name) / "partial-abort.jsonl"
        events = [
            {"type": "event_msg", "payload": {"type": "user_message", "client_id": "partial", "message": "Start work"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"text": "Partial result"}]}},
            {"type": "event_msg", "payload": {"type": "turn_aborted"}},
        ]
        rollout.write_text("\n".join(json.dumps(item) for item in events), encoding="utf-8")
        result = ct.sync_rollout_prompts(self.db, self.store, "partial-session", rollout)
        self.assertEqual(result["created"], 1)
        row = self.db.execute(
            "SELECT response_summary FROM turns WHERE session_id='partial-session'"
        ).fetchone()
        self.assertEqual(row[0], "Partial result")

    def test_rollout_sync_reconciles_legacy_empty_abort_when_file_is_unchanged(self) -> None:
        rollout = Path(self.temp.name) / "legacy-abort.jsonl"
        events = [
            {"type": "event_msg", "payload": {"type": "user_message", "client_id": "legacy", "message": "Old request"}},
            {"type": "event_msg", "payload": {"type": "turn_aborted"}},
        ]
        rollout.write_text("\n".join(json.dumps(item) for item in events), encoding="utf-8")
        legacy = ct.commit_turn(self.db, self.store, {
            "event_key": "codex-prompt:legacy-session:legacy", "session_id": "legacy-session",
            "user_intent": "Old request", "response_summary": "", "consolidation_status": "pending",
        })
        stat = rollout.stat()
        self.db.execute(
            "INSERT INTO session_sync VALUES(?,?,?,?,?)",
            ("legacy-session", 1, stat.st_size, stat.st_mtime_ns, ct.now()),
        )
        self.db.commit()
        result = ct.sync_rollout_prompts(self.db, self.store, "legacy-session", rollout)
        self.assertEqual(result["created"], 0)
        self.assertIsNone(self.db.execute(
            "SELECT id FROM turns WHERE id=?", (legacy["turn_id"],)
        ).fetchone())

    def test_rollout_sync_revisits_latest_turn_and_updates_local_tokens(self) -> None:
        rollout = Path(self.temp.name) / "growing-rollout.jsonl"
        user = {
            "timestamp": "2026-01-01T00:00:00Z", "type": "event_msg",
            "payload": {"type": "user_message", "client_id": "latest", "message": "Build it"},
        }
        rollout.write_text(json.dumps(user), encoding="utf-8")
        initial = ct.sync_rollout_prompts(self.db, self.store, "growing-session", rollout)
        self.assertEqual(initial["scanned_bytes"], rollout.stat().st_size)
        row = self.db.execute(
            "SELECT response_summary FROM turns WHERE session_id='growing-session'"
        ).fetchone()
        self.assertEqual(row[0], "")
        with rollout.open("a", encoding="utf-8") as handle:
            handle.write("\n" + json.dumps({
                "timestamp": "2026-01-01T00:00:03Z", "type": "response_item",
                "payload": {"type": "message", "role": "assistant", "content": [{"text": "Implemented it"}]},
            }))
            handle.write("\n" + json.dumps({
                "type": "event_msg", "payload": {"type": "token_count", "info": {
                    "last_token_usage": {"input_tokens": 54321}, "model_context_window": 272000,
                }},
            }))
        grown = ct.sync_rollout_prompts(self.db, self.store, "growing-session", rollout)
        self.assertLess(grown["scanned_bytes"], rollout.stat().st_size)
        row = self.db.execute(
            "SELECT response_summary FROM turns WHERE session_id='growing-session'"
        ).fetchone()
        metrics = self.db.execute(
            "SELECT estimated_context_tokens,turn_count FROM session_metrics WHERE session_id='growing-session'"
        ).fetchone()
        self.assertEqual(row[0], "Implemented it")
        self.assertEqual(tuple(metrics), (54321, 1))

    def test_background_batch_claims_complete_turns_and_consolidates(self) -> None:
        ct.save_config(self.store, {"consolidate_every": 2, "batch_ai_auto_start": False})
        inbox, _ = ct.create_topic(self.db, self.store, "待归类", "Pending inbox")
        target, _ = ct.create_topic(self.db, self.store, "项目探索", "Long-running projects")
        ct.attach_session(self.db, self.store, "batch-session", inbox)
        completed = []
        for index in range(2):
            completed.append(ct.commit_turn(self.db, self.store, {
                "event_key": f"batch-complete-{index}", "session_id": "batch-session",
                "user_intent": f"Project step {index}", "response_summary": f"Result {index}",
                "consolidation_status": "pending",
            })["turn_id"])
        incomplete = ct.commit_turn(self.db, self.store, {
            "event_key": "batch-incomplete", "session_id": "batch-session",
            "user_intent": "Still running", "response_summary": "", "consolidation_status": "pending",
        })["turn_id"]
        state = ct.ensure_due_batch_job(self.db, self.store, launch=False)
        self.assertEqual(state["status"], "queued")
        self.assertEqual(set(state["turn_ids"]), set(completed))
        result = {"job_id": state["id"], "items": [{
            "turn_id": turn_id, "topic_id": target, "new_topic_title": "",
            "new_topic_summary": "", "branch_title": "Context Tree",
            "nodes": [{"type": "result", "label": f"Saved {index}", "capsule": "Durable result", "status": "active"}],
            "invalidate": [], "edges": [], "outcome_summary": f"Result {index}", "next_step": "Continue",
        } for index, turn_id in enumerate(completed)]}
        applied = ct.apply_batch_result(self.db, self.store, state["id"], result)
        self.assertEqual(applied["affected_topic_ids"], [target])
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM turns WHERE consolidation_status='consolidated' AND id IN (?,?)", completed).fetchone()[0], 2
        )
        self.assertEqual(
            self.db.execute("SELECT consolidation_status FROM turns WHERE id=?", (incomplete,)).fetchone()[0], "pending"
        )
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM nodes WHERE topic_id=? AND type='result'", (target,)).fetchone()[0], 2
        )

    def test_handoff_enrichment_is_node_specific_and_preserves_exact_data(self) -> None:
        topic_id, _ = ct.create_topic(self.db, self.store, "节点整理", "Distill node handoffs")
        ct.attach_session(self.db, self.store, "handoff-session", topic_id)
        created = ct.commit_turn(self.db, self.store, {
            "event_key": "handoff-turn", "session_id": "handoff-session",
            "user_intent": "实现并验证节点整理", "response_summary": "完成两项独立工作",
            "nodes": [
                {"id": "method-node", "type": "decision", "label": "选择方法", "capsule": "旧方法摘要",
                 "exact_data": {"path": "D:/work/input.json"}},
                {"id": "result-node", "type": "result", "label": "验证结果", "capsule": "旧结果摘要",
                 "exact_data": {"measurement": "39 tests passed"}},
            ],
        })
        node_rows = self.db.execute(
            "SELECT id,label FROM nodes WHERE created_turn_id=? ORDER BY label", (created["turn_id"],),
        ).fetchall()
        by_label = {row["label"]: row["id"] for row in node_rows}
        base = {field: "" for field in ct.HANDOFF_FIELDS}
        method_handoff = {**base, "objective": "建立节点级整理", "method": "读取 D:/work/input.json",
                          "results": "读取 D:/work/input.json"}
        result_handoff = {**base, "objective": "确认实现有效", "results": "39 tests passed"}
        supplied = [
                {"node_id": by_label["选择方法"], "capsule": "确定按节点分别提纯。", "handoff": method_handoff},
                {"node_id": by_label["验证结果"], "capsule": "测试结果已确认。", "handoff": result_handoff},
        ]
        supplied.extend(
            {"node_id": row["id"], "capsule": "本轮目标已提纯。", "handoff": base}
            for row in node_rows if row["id"] not in {item["node_id"] for item in supplied}
        )
        updated = ct.apply_handoff_enrichment(
            self.db, topic_id, [row["id"] for row in node_rows], {"items": supplied},
        )
        self.assertEqual(updated, len(node_rows))
        method = self.db.execute(
            "SELECT capsule,exact_data_json FROM nodes WHERE id=?", (by_label["选择方法"],),
        ).fetchone()
        exact = json.loads(method["exact_data_json"])
        self.assertEqual(method["capsule"], "确定按节点分别提纯。")
        self.assertEqual(exact["path"], "D:/work/input.json")
        self.assertEqual(exact["handoff"]["method"], "读取 D:/work/input.json")
        self.assertEqual(exact["handoff"]["results"], "")
        self.assertEqual(exact["handoff_source"]["kind"], "ai_enriched")

    def test_handoff_enrichment_schema_constrains_the_exact_node_batch(self) -> None:
        schema = ct.handoff_enrichment_output_schema(["node-a", "node-b"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        items = schema["properties"]["items"]
        self.assertEqual((items["minItems"], items["maxItems"]), (2, 2))
        node_id = items["items"]["properties"]["node_id"]
        self.assertEqual(node_id["enum"], ["node-a", "node-b"])
        handoff = items["items"]["properties"]["handoff"]
        self.assertFalse(handoff["additionalProperties"])
        self.assertEqual(set(handoff["required"]), set(ct.HANDOFF_FIELDS))

    def test_handoff_enrichment_requires_every_node_and_skips_completed_turns(self) -> None:
        topic_id, _ = ct.create_topic(self.db, self.store, "完整性", "Require complete enrichment")
        ct.attach_session(self.db, self.store, "complete-session", topic_id)
        created = ct.commit_turn(self.db, self.store, {
            "event_key": "complete-turn", "session_id": "complete-session",
            "user_intent": "整理两个节点", "response_summary": "已有结果",
            "nodes": [
                {"type": "decision", "label": "节点一", "capsule": "一"},
                {"type": "result", "label": "节点二", "capsule": "二"},
            ],
        })
        nodes = self.db.execute(
            "SELECT id FROM nodes WHERE created_turn_id=? ORDER BY id", (created["turn_id"],),
        ).fetchall()
        complete = {field: "" for field in ct.HANDOFF_FIELDS}
        with self.assertRaisesRegex(ValueError, "every requested node exactly once"):
            ct.apply_handoff_enrichment(self.db, topic_id, [node["id"] for node in nodes], {
                "items": [{"node_id": nodes[0]["id"], "capsule": "只返回一个", "handoff": complete}],
            })
        ct.apply_handoff_enrichment(self.db, topic_id, [node["id"] for node in nodes], {
            "items": [
                {"node_id": node["id"], "capsule": f"整理 {index}", "handoff": complete}
                for index, node in enumerate(nodes)
            ],
        })
        with mock.patch.object(ct, "rehydrate_topic_turn_details", return_value={"updated": 0}), mock.patch.object(
            ct, "handoff_enrichment_payload",
        ) as payload:
            result = ct.enrich_topic_handoffs(self.db, self.store, topic_id)
        self.assertEqual(result["turn_count"], 0)
        self.assertEqual(result["updated_nodes"], 0)
        payload.assert_not_called()

    def test_settings_separates_ready_recording_and_processing_counts(self) -> None:
        ct.save_config(self.store, {"consolidate_every": 5, "batch_ai_auto_start": False})
        topic_id, _ = ct.create_topic(self.db, self.store, "待归类", "Pending inbox")
        ct.attach_session(self.db, self.store, "count-session", topic_id)
        for index, response in enumerate(("Done one", "Done two", "")):
            ct.commit_turn(self.db, self.store, {
                "event_key": f"count-{index}", "session_id": "count-session",
                "user_intent": f"Step {index}", "response_summary": response,
                "consolidation_status": "pending",
            })
        active = [{
            "session_id": "count-session", "topic_id": topic_id,
            "topic_title": "待归类", "pending_count": 3,
            "active_turn_id": self.db.execute(
                "SELECT id FROM turns WHERE event_key='count-1'"
            ).fetchone()[0],
        }]
        with mock.patch.object(ct, "usage_payload", return_value={}), mock.patch.object(
            ct, "active_codex_sessions", return_value=active,
        ):
            payload = ct.settings_payload(self.db, self.store)
        self.assertEqual(payload["summary"]["ready_count"], 1)
        self.assertEqual(payload["summary"]["recording_count"], 2)
        self.assertEqual(payload["active_sessions"][0]["ready_count"], 1)
        self.assertEqual(payload["active_sessions"][0]["recording_count"], 2)

    def test_session_cost_profile_groups_requests_by_compaction_epoch(self) -> None:
        runtime = {
            "model": "gpt-test", "reasoning_effort": "medium",
            "context_window_tokens": 1000,
            "current_turn_started_at": "2026-01-01T00:10:00Z",
            "compaction_timestamps": ["2026-01-01T00:03:30Z"],
        }
        requests = [
            {"model": "gpt-test", "reasoning_effort": "medium", "service_tier": "default", "context_tokens": 100, "cache_read_tokens": 50, "cost": 0.02, "context_cost": 0.01, "first_token_ms": 1000, "duration_ms": 4000, "created_at": "2026-01-01T00:01:00Z"},
            {"model": "gpt-test", "reasoning_effort": "medium", "service_tier": "default", "context_tokens": 200, "cache_read_tokens": 100, "cost": 0.04, "context_cost": 0.02, "first_token_ms": 2000, "duration_ms": 6000, "created_at": "2026-01-01T00:02:00Z"},
            {"model": "gpt-test", "reasoning_effort": "medium", "service_tier": "default", "context_tokens": 300, "cache_read_tokens": 150, "cost": 0.06, "context_cost": 0.03, "first_token_ms": 3000, "duration_ms": 8000, "created_at": "2026-01-01T00:04:00Z"},
            {"model": "gpt-test", "reasoning_effort": "high", "service_tier": "default", "context_tokens": 400, "cache_read_tokens": 200, "cost": 0.08, "context_cost": 0.04, "first_token_ms": 4000, "duration_ms": 10000, "created_at": "2026-01-01T00:05:00Z"},
            {"model": "gpt-test", "reasoning_effort": "medium", "service_tier": "default", "context_tokens": 600, "cache_read_tokens": 300, "cost": 0.10, "context_cost": 0.05, "first_token_ms": 5000, "duration_ms": 12000, "created_at": "2026-01-01T00:11:00Z"},
            {"model": "gpt-test", "reasoning_effort": "medium", "service_tier": "default", "context_tokens": 650, "cache_read_tokens": 325, "cost": 0.14, "context_cost": 0.07, "first_token_ms": 7000, "duration_ms": 14000, "created_at": "2026-01-01T00:12:00Z"},
        ]
        profile = ct.session_cost_profile(requests, runtime)
        self.assertEqual(profile["cost_profile_status"], "ready")
        self.assertEqual(profile["baseline_request_count"], 2)
        self.assertEqual(profile["turn_cost_request_count"], 2)
        self.assertEqual(len(profile["epoch_summaries"]), 2)
        self.assertEqual(profile["epoch_summaries"][0]["request_count"], 2)
        self.assertEqual(profile["epoch_summaries"][1]["request_count"], 4)
        self.assertAlmostEqual(profile["baseline_avg_cost"], 0.03)
        self.assertAlmostEqual(profile["current_epoch_avg_cost"], 0.095)
        self.assertAlmostEqual(profile["turn_avg_cost"], 0.12)
        self.assertAlmostEqual(profile["turn_extra_avg_cost"], 0.09)
        self.assertAlmostEqual(profile["turn_avg_first_token_ms"], 6000)
        self.assertAlmostEqual(profile["turn_avg_duration_ms"], 13000)
        self.assertAlmostEqual(profile["turn_total_cost"], 0.24)
        self.assertAlmostEqual(profile["turn_cache_hit_percent"], 50.0)
        self.assertAlmostEqual(profile["current_epoch_total_cost"], 0.38)
        self.assertAlmostEqual(profile["current_epoch_cache_hit_percent"], 50.0)
        self.assertEqual(profile["current_epoch_request_count"], 4)
        self.assertEqual(profile["stage_current_request_count"], 4)

    def test_remote_request_history_uses_stable_thread_route_and_cache(self) -> None:
        paths: list[str] = []

        def request(_store: Path, path: str) -> dict[str, object]:
            paths.append(path)
            return {"requests": [{"request_id": "req-window-0"}, {"request_id": "req-window-2"}]}

        ct.save_config(self.store, {"usage_server_url": "https://usage.example"})
        ct.save_usage_credentials(self.store, "secret-key-1234")
        ct._REMOTE_REQUEST_HISTORY_CACHE.clear()
        with mock.patch.object(ct, "remote_usage_request", side_effect=request):
            first = ct.remote_usage_request_history(self.store, "thread-a", max_age_seconds=30)
            second = ct.remote_usage_request_history(self.store, "thread-a", max_age_seconds=30)
        self.assertEqual(first, second)
        self.assertEqual(paths, ["/v1/sub2api/usage/threads/thread-a/requests?limit=10000"])

    def test_session_cost_profile_uses_earliest_recorded_stage_for_partial_history(self) -> None:
        runtime = {
            "model": "gpt-test", "reasoning_effort": "medium",
            "current_turn_started_at": "2026-01-01T00:10:00Z",
            "compaction_timestamps": ["2026-01-01T00:03:00Z", "2026-01-01T00:06:00Z"],
        }
        requests = [
            {"cost": 0.04, "context_cost": 0.03, "created_at": "2026-01-01T00:04:00Z"},
            {"cost": 0.08, "context_cost": 0.06, "created_at": "2026-01-01T00:11:00Z"},
        ]
        profile = ct.session_cost_profile(requests, runtime)
        self.assertEqual(profile["cost_profile_status"], "partial_history")
        self.assertEqual(profile["baseline_request_count"], 0)
        self.assertEqual(profile["reference_epoch_index"], 1)
        self.assertAlmostEqual(profile["reference_avg_cost"], 0.04)
        self.assertAlmostEqual(profile["reference_epoch_extra_avg_cost"], 0.04)

    def test_goal_or_long_turn_is_queued_without_waiting_for_global_interval(self) -> None:
        ct.save_config(self.store, {"consolidate_every": 5, "batch_ai_auto_start": False})
        topic_id, _ = ct.create_topic(self.db, self.store, "Long task", "Detailed work")
        ct.attach_session(self.db, self.store, "goal-session", topic_id)
        result = ct.commit_turn(self.db, self.store, {
            "event_key": "goal-long", "session_id": "goal-session",
            "user_intent": "Complete a multi-step goal", "response_summary": "Completed many steps",
            "consolidation_status": "pending",
        })
        ct.queue_turn_detail(
            self.db, result["turn_id"], "Detailed goal input", "Detailed result " * 100,
            collaboration_mode="goal", duration_seconds=7200,
        )
        state = ct.ensure_due_batch_job(self.db, self.store, launch=False)
        self.assertEqual(state["status"], "queued")
        self.assertEqual(state["turn_ids"], [result["turn_id"]])
        payload = ct.batch_job_payload(self.db, state["id"])
        self.assertEqual(payload["items"][0]["detail_priority"], "long")
        self.assertEqual(payload["items"][0]["duration_seconds"], 7200)

    def test_branch_context_keeps_subtopics_isolated(self) -> None:
        topic_id, main_branch = ct.create_topic(self.db, self.store, "Research", "Paper work")
        branch_id = ct.uid("branch")
        self.db.execute(
            "INSERT INTO branches VALUES(?,?,?,?,?,?,?)",
            (branch_id, topic_id, main_branch, "Inversion", "active", None, ct.now()),
        )
        self.db.commit()
        ct.attach_session(self.db, self.store, "main-session", topic_id, main_branch)
        ct.attach_session(self.db, self.store, "branch-session", topic_id, branch_id)
        ct.commit_turn(self.db, self.store, {
            "event_key": "main-pending", "session_id": "main-session",
            "user_intent": "Write introduction", "response_summary": "Drafted introduction",
            "consolidation_status": "pending",
        })
        ct.commit_turn(self.db, self.store, {
            "event_key": "branch-pending", "session_id": "branch-session",
            "user_intent": "Tune inversion parameters", "response_summary": "Compared optimizers",
            "consolidation_status": "pending",
        })
        context = ct.active_context(self.db, topic_id, 5, branch_id)
        self.assertEqual(context["branch_id"], branch_id)
        self.assertEqual(context["pending_count"], 1)
        self.assertEqual(context["pending_tail"][0]["user_intent"], "Tune inversion parameters")

    def test_one_shot_resume_pointer_injects_selected_node_and_clears(self) -> None:
        topic_id, main_branch = ct.create_topic(self.db, self.store, "Research", "Paper work")
        branch_id = ct.uid("branch")
        self.db.execute(
            "INSERT INTO branches VALUES(?,?,?,?,?,?,?)",
            (branch_id, topic_id, main_branch, "Inversion", "active", None, ct.now()),
        )
        self.db.commit()
        ct.attach_session(self.db, self.store, "old-session", topic_id, branch_id)
        ct.commit_turn(self.db, self.store, {
            "event_key": "resume-source", "session_id": "old-session",
            "user_intent": "Tune inversion", "response_summary": "Optimizer B converged",
            "nodes": [{
                "id": "resume-node", "type": "result", "label": "Optimizer B converged",
                "capsule": "Detailed convergence result for the next task",
                "exact_data": {"iteration": 42},
            }],
        })
        ct.save_config(self.store, {"resume_pointer": {
            "topic_id": topic_id, "branch_id": branch_id, "node_id": "resume-node", "mode": "once",
        }})
        payload = json.dumps({
            "hook_event_name": "SessionStart", "cwd": self.temp.name,
            "context_tree_home": str(self.store), "session_id": "new-session",
        })
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "hook", "session-start"], input=payload,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Selected continuation node", result.stdout)
        self.assertIn("Detailed convergence result for the next task", result.stdout)
        self.assertIsNone(ct.load_config(self.store)["resume_pointer"])
        attached = self.db.execute("SELECT branch_id FROM sessions WHERE id='new-session'").fetchone()
        self.assertEqual(attached["branch_id"], branch_id)

    def test_fresh_session_pointer_skips_restored_session_then_claims_new_id(self) -> None:
        topic_id, branch_id = ct.create_topic(self.db, self.store, "Research", "Paper work")
        ct.save_config(self.store, {"resume_pointer": {
            "topic_id": topic_id, "branch_id": branch_id, "node_id": "", "mode": "once",
            "new_session_only": True, "known_session_ids": ["restored-session"],
        }})
        restored_payload = json.dumps({
            "hook_event_name": "SessionStart", "cwd": self.temp.name,
            "context_tree_home": str(self.store), "session_id": "restored-session",
        })
        restored = subprocess.run(
            [sys.executable, str(SCRIPT), "hook", "session-start"], input=restored_payload,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertIsNotNone(ct.load_config(self.store)["resume_pointer"])
        restored_session = self.db.execute(
            "SELECT topic_id FROM sessions WHERE id='restored-session'"
        ).fetchone()
        self.assertIsNone(restored_session["topic_id"])

        fresh_payload = json.dumps({
            "hook_event_name": "SessionStart", "cwd": self.temp.name,
            "context_tree_home": str(self.store), "session_id": "fresh-session",
        })
        fresh = subprocess.run(
            [sys.executable, str(SCRIPT), "hook", "session-start"], input=fresh_payload,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(fresh.returncode, 0, fresh.stderr)
        self.assertIn("No passphrase", fresh.stdout)
        self.assertIsNone(ct.load_config(self.store)["resume_pointer"])
        fresh_session = self.db.execute(
            "SELECT topic_id,branch_id FROM sessions WHERE id='fresh-session'"
        ).fetchone()
        self.assertEqual(tuple(fresh_session), (topic_id, branch_id))

    def test_fresh_session_pointer_claims_on_first_prompt_when_session_start_missing(self) -> None:
        topic_id, branch_id = ct.create_topic(self.db, self.store, "Research", "Paper work")
        ct.save_config(self.store, {"resume_pointer": {
            "topic_id": topic_id, "branch_id": branch_id, "node_id": "", "mode": "once",
            "new_session_only": True, "known_session_ids": ["old-session"],
        }})
        prompt_payload = json.dumps({
            "hook_event_name": "UserPromptSubmit", "cwd": self.temp.name,
            "context_tree_home": str(self.store), "session_id": "fresh-prompt-session",
            "prompt": "继续",
        })
        prompt = subprocess.run(
            [sys.executable, str(SCRIPT), "hook", "prompt"], input=prompt_payload,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(prompt.returncode, 0, prompt.stderr)
        self.assertIn("No passphrase", prompt.stdout)
        self.assertIsNone(ct.load_config(self.store)["resume_pointer"])
        fresh_session = self.db.execute(
            "SELECT topic_id,branch_id FROM sessions WHERE id='fresh-prompt-session'"
        ).fetchone()
        self.assertEqual(tuple(fresh_session), (topic_id, branch_id))

    def test_hook_command_honors_store_argument(self) -> None:
        topic_id, branch_id = ct.create_topic(self.db, self.store, "Research", "Paper work")
        ct.save_config(self.store, {"resume_pointer": {
            "topic_id": topic_id, "branch_id": branch_id, "node_id": "", "mode": "once",
            "new_session_only": True, "known_session_ids": [],
        }})
        prompt_payload = json.dumps({
            "hook_event_name": "UserPromptSubmit", "cwd": self.temp.name,
            "session_id": "fresh-store-arg-session", "prompt": "继续",
        })
        prompt = subprocess.run(
            [sys.executable, str(SCRIPT), "--store", str(self.store), "hook", "prompt"],
            input=prompt_payload, text=True, capture_output=True, check=False,
        )
        self.assertEqual(prompt.returncode, 0, prompt.stderr)
        self.assertIn("No passphrase", prompt.stdout)
        self.assertIsNone(ct.load_config(self.store)["resume_pointer"])

    def test_historical_backfill_inserts_between_existing_nodes(self) -> None:
        topic_id, branch_id = ct.create_topic(self.db, self.store, "Project history", "Chronological work")
        ct.attach_session(self.db, self.store, "existing-session", topic_id)
        for key, date, label in (
            ("early", "2026-01-10T00:00:00+00:00", "Early result"),
            ("late", "2026-01-30T00:00:00+00:00", "Latest result"),
        ):
            ct.commit_turn(self.db, self.store, {
                "event_key": key, "session_id": "existing-session", "topic_id": topic_id,
                "branch_id": branch_id, "user_intent": label, "response_summary": label,
                "outcome_summary": label, "occurred_at": date,
                "nodes": [{"type": "goal" if key == "early" else "result", "label": label, "capsule": label}],
            })
        timestamp = ct.now()
        self.db.execute(
            "INSERT INTO backfill_jobs VALUES(?,?,?,?,?,?,?,?,?)",
            ("job-history", "historic-session", "Historic session", "queued", 1, 0, "", timestamp, timestamp),
        )
        self.db.execute(
            "INSERT INTO backfill_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("item-middle", "job-history", "source-middle", 1, "2026-01-20T00:00:00+00:00",
             "Middle work", "Middle result", "{}", "queued", None, None, None, "", timestamp, timestamp),
        )
        self.db.commit()
        result = ct.apply_backfill_job(self.db, self.store, {
            "job_id": "job-history",
            "items": [{
                "item_id": "item-middle", "topic_id": topic_id,
                "nodes": [{"type": "result", "label": "Middle result", "capsule": "Inserted history"}],
            }],
        })
        self.assertEqual(result["job"]["status"], "completed")
        labels = [row[0] for row in self.db.execute(
            "SELECT label FROM nodes WHERE topic_id=? ORDER BY occurred_at,created_at,id", (topic_id,)
        )]
        self.assertEqual(labels, ["Early result", "Middle result", "Latest result"])
        timeline = self.db.execute(
            "SELECT COUNT(*) FROM edges WHERE topic_id=? AND relation='timeline_next'", (topic_id,)
        ).fetchone()[0]
        self.assertEqual(timeline, 2)
        snapshot = ct.snapshot_projection(self.db, topic_id, branch_id)
        self.assertEqual(snapshot["current_state"], "Latest result")

    def test_rollout_history_preserves_original_turn_times(self) -> None:
        rollout = Path(self.temp.name) / "history.jsonl"
        events = [
            {"timestamp": "2026-01-02T03:04:05Z", "type": "event_msg", "payload": {"type": "user_message", "client_id": "one", "message": "First"}},
            {"timestamp": "2026-01-02T03:04:06Z", "type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"text": "Done"}]}},
            {"timestamp": "2026-01-02T03:04:07Z", "type": "event_msg", "payload": {"type": "task_complete"}},
            {"timestamp": "2026-01-01T01:00:00Z", "type": "event_msg", "payload": {"type": "user_message", "client_id": "two", "message": "Second"}},
            {"timestamp": "2026-01-01T01:00:01Z", "type": "event_msg", "payload": {"type": "turn_aborted"}},
        ]
        rollout.write_text("\n".join(json.dumps(item) for item in events), encoding="utf-8")
        turns = ct.rollout_history_turns(rollout)
        self.assertEqual(turns[0]["source_status"], "completed")
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["occurred_at"], "2026-01-02T03:04:05.000+00:00")

    def test_rollout_history_keeps_aborted_turn_with_partial_output(self) -> None:
        rollout = Path(self.temp.name) / "history-partial.jsonl"
        events = [
            {"type": "event_msg", "payload": {"type": "user_message", "client_id": "partial", "message": "First"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"text": "Partial"}]}},
            {"type": "event_msg", "payload": {"type": "turn_aborted"}},
        ]
        rollout.write_text("\n".join(json.dumps(item) for item in events), encoding="utf-8")
        turns = ct.rollout_history_turns(rollout)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["source_status"], "aborted")
        self.assertEqual(turns[0]["response_summary"], "Partial")

    def test_create_backfill_job_deduplicates_active_session_import(self) -> None:
        rollout = Path(self.temp.name) / "import.jsonl"
        rollout.write_text("\n".join(json.dumps(item) for item in [
            {"timestamp": "2026-01-01T00:00:00Z", "type": "event_msg", "payload": {"type": "user_message", "client_id": "a", "message": "Alpha"}},
            {"timestamp": "2026-01-02T00:00:00Z", "type": "event_msg", "payload": {"type": "user_message", "client_id": "b", "message": "Beta"}},
        ]), encoding="utf-8")
        with mock.patch.object(ct, "find_codex_rollout_by_id", return_value=(rollout, "Imported title", self.temp.name)):
            first = ct.create_backfill_job(self.db, self.store, "session-import")
            second = ct.create_backfill_job(self.db, self.store, "session-import")
        self.assertEqual(first["job"]["id"], second["job"]["id"])
        self.assertEqual(first["job"]["total_items"], 2)
        self.assertEqual(len(first["items"]), 2)
        self.assertIn("untrusted data", ct.backfill_ai_prompt(first))
        self.assertEqual(ct.backfill_output_schema()["properties"]["items"]["type"], "array")

    def test_topic_settings_hook_opens_local_panel_without_topic_pollution(self) -> None:
        first, _ = ct.create_topic(self.db, self.store, "Older topic", "Still selectable")
        second, _ = ct.create_topic(self.db, self.store, "Newer topic", "Also selectable")
        payload = json.dumps({
            "hook_event_name": "UserPromptSubmit", "cwd": self.temp.name,
            "context_tree_home": str(self.store),
            "session_id": "settings-session", "prompt": "打开主题设置，显示所有主题",
        })
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "hook", "prompt"], input=payload,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("local settings panel", context)
        self.assertNotIn(first, context)
        self.assertNotIn(second, context)

    def test_sticky_topic_attaches_on_fresh_session_and_due_batch_is_injected(self) -> None:
        topic_id, _ = ct.create_topic(self.db, self.store, "Sticky work", "Keep this topic selected")
        ct.save_config(self.store, {
            "routing_mode": "sticky", "sticky_topic_id": topic_id,
            "consolidate_every": 3, "route_every": 3,
        })
        start_payload = json.dumps({
            "hook_event_name": "SessionStart", "cwd": self.temp.name,
            "context_tree_home": str(self.store),
            "session_id": "sticky-session",
        })
        start = subprocess.run(
            [sys.executable, str(SCRIPT), "hook", "session-start"], input=start_payload,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(start.returncode, 0, start.stderr)
        self.assertIn(topic_id, start.stdout)
        for index in range(3):
            ct.commit_turn(self.db, self.store, {
                "event_key": f"sticky-{index}", "session_id": "sticky-session",
                "user_intent": f"Sticky step {index}", "response_summary": "Saved locally",
                "consolidation_status": "pending",
            })
        prompt_payload = json.dumps({
            "hook_event_name": "UserPromptSubmit", "cwd": self.temp.name,
            "context_tree_home": str(self.store),
            "session_id": "sticky-session", "prompt": "Continue this work",
        })
        prompt = subprocess.run(
            [sys.executable, str(SCRIPT), "hook", "prompt"], input=prompt_payload,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(prompt.returncode, 0, prompt.stderr)
        context = json.loads(prompt.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("batch due", context)
        self.assertIn("Sticky step 0", context)

    def test_auto_mode_injects_due_batch_on_first_fresh_prompt(self) -> None:
        topic_id, _ = ct.create_topic(
            self.db, self.store, "Seismic waveform analysis", "Analyze earthquake station waveforms"
        )
        ct.attach_session(self.db, self.store, "old-auto-session", topic_id)
        for index in range(3):
            ct.commit_turn(self.db, self.store, {
                "event_key": f"auto-due-{index}", "session_id": "old-auto-session",
                "user_intent": f"Analyze seismic waveform batch {index}",
                "response_summary": f"Waveform result {index}",
                "consolidation_status": "pending",
            })
        payload = json.dumps({
            "hook_event_name": "UserPromptSubmit", "cwd": self.temp.name,
            "context_tree_home": str(self.store),
            "session_id": "fresh-auto-session",
            "prompt": "Continue the seismic waveform analysis topic",
        })
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "hook", "prompt"], input=payload,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn(f"High-confidence automatic candidate: {topic_id}", context)
        self.assertIn("batch due", context)
        self.assertIn("Analyze seismic waveform batch 0", context)

    def test_local_settings_ui_changes_topics_without_ai_context(self) -> None:
        process = subprocess.Popen(
            [sys.executable, str(SCRIPT), "--store", str(self.store), "ui", "--port", "0", "--no-open"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            assert process.stdout
            url = process.stdout.readline().strip()
            self.assertTrue(url.startswith("http://127.0.0.1:"), url)
            html = urllib.request.urlopen(url, timeout=5).read().decode("utf-8")
            self.assertIn('id="backfill-session"', html)
            self.assertIn('id="float-persistent"', html)
            token_match = __import__("re").search(r'const TOKEN = "([^"]+)"', html)
            self.assertIsNotNone(token_match)
            token = token_match.group(1)

            def request(path: str, method: str = "GET", body: dict | None = None) -> dict:
                data = json.dumps(body).encode("utf-8") if body is not None else None
                req = urllib.request.Request(
                    url + path.lstrip("/"), data=data, method=method,
                    headers={"Content-Type": "application/json", "X-Context-Tree-Token": token},
                )
                return json.loads(urllib.request.urlopen(req, timeout=5).read().decode("utf-8"))

            initial = request("/api/state")
            self.assertFalse(initial["config"]["float_persistent"])
            persistent = request("/api/float-settings", "POST", {"persistent": True})
            self.assertTrue(persistent["config"]["float_persistent"])
            hidden = request("/api/float-settings", "POST", {"persistent": False})
            self.assertFalse(hidden["config"]["float_persistent"])

            created = request("/api/topics", "POST", {
                "title": "Local UI topic", "summary": "Created without an AI turn", "select": True,
            })
            topic_id = created["config"]["sticky_topic_id"]
            self.assertEqual(created["config"]["routing_mode"], "sticky")
            updated = request("/api/routing", "POST", {
                "mode": "auto", "topic_id": None, "every": 5,
            })
            self.assertEqual(updated["config"]["routing_mode"], "auto")
            self.assertEqual(updated["config"]["consolidate_every"], 5)
            self.assertTrue(any(topic["id"] == topic_id for topic in updated["topics"]))
            graph_html = urllib.request.urlopen(
                url + f"graph?topic={topic_id}", timeout=5
            ).read().decode("utf-8")
            self.assertIn("Context Tree Graph", graph_html)
            graph = request(f"/api/graph?topic={topic_id}")
            self.assertEqual(graph["topic"]["id"], topic_id)
            self.assertEqual(graph["branches"][0]["topic_id"], topic_id)
            resume = request("/api/resume", "POST", {
                "topic_id": topic_id, "branch_id": graph["branches"][0]["id"],
                "node_id": "", "mode": "once",
            })
            self.assertEqual(resume["status"], "ready")
            pointer = ct.load_config(self.store)["resume_pointer"]
            self.assertEqual(pointer["branch_id"], graph["branches"][0]["id"])
            self.assertTrue(pointer["new_session_only"])
            self.assertIsInstance(pointer["known_session_ids"], list)
        finally:
            process.terminate()
            process.wait(timeout=5)
            if process.stdin:
                process.stdin.close()
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()

    def test_real_usage_keeps_sessions_isolated_and_hides_api_key(self) -> None:
        class UsageHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                return

            def do_GET(self) -> None:
                if self.headers.get("Authorization") != "Bearer secret-key-1234":
                    self.send_response(401)
                    self.end_headers()
                    return
                if self.path.startswith("/v1/sub2api/usage/sessions?"):
                    body = {"mode": "real", "sessions": [
                        {"session_id": "thread-a:1", "thread_id": "thread-a", "window_slot": "1", "context_tokens": 900, "request_count": 4},
                        {"session_id": "thread-a:2", "thread_id": "thread-a", "window_slot": "2", "context_tokens": 200, "request_count": 2},
                    ]}
                else:
                    body = {"mode": "real", "session_id": "thread-a:1", "requests": [
                        {"request_id": "req-1", "context_tokens": 900, "cost": 0.1}
                    ]}
                encoded = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), UsageHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            ct.save_config(self.store, {
                "usage_server_url": f"http://127.0.0.1:{server.server_port}",
                "usage_context_window_tokens": 1000,
                "usage_warning_ratio": 0.7,
                "usage_critical_ratio": 0.85,
            })
            ct.save_usage_credentials(self.store, "secret-key-1234")
            payload = ct.usage_payload(self.db, self.store)
            self.assertEqual(payload["mode"], "real")
            self.assertEqual([item["session_id"] for item in payload["sessions"]], ["thread-a:1", "thread-a:2"])
            self.assertEqual(payload["sessions"][0]["level"], "critical")
            self.assertEqual(payload["sessions"][1]["level"], "normal")
            self.assertNotIn("secret-key-1234", json.dumps(payload))
            self.assertNotIn("api_key", (self.store / "config.json").read_text())
            requests = ct.usage_requests_payload(self.store, "thread-a:1")
            self.assertEqual(requests["requests"][0]["request_id"], "req-1")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_usage_session_matches_latest_window_slot_by_thread_id(self) -> None:
        usage = {"sessions": [
            {"session_id": "thread-a:1", "thread_id": "thread-a", "last_active_at": "2026-01-01T00:00:00Z"},
            {"session_id": "thread-a:2", "thread_id": "thread-a", "last_active_at": "2026-01-02T00:00:00Z"},
            {"session_id": "thread-b:1", "thread_id": "thread-b", "last_active_at": "2026-01-03T00:00:00Z"},
        ]}
        matched = ct.usage_session_for_thread(usage, "thread-a")
        self.assertEqual(matched["session_id"], "thread-a:2")
        exact = ct.usage_session_for_thread(usage, "thread-a:1")
        self.assertEqual(exact["session_id"], "thread-a:1")

    def test_usage_falls_back_to_local_session_estimate(self) -> None:
        ct.ensure_session(self.db, "local-session", self.store)
        ct.update_session_metrics(self.db, "local-session", 81000, 12)
        payload = ct.usage_payload(self.db, self.store)
        self.assertEqual(payload["mode"], "estimated")
        self.assertEqual(payload["sessions"][0]["session_id"], "local-session")
        self.assertEqual(payload["sessions"][0]["context_tokens"], 81000)

    def test_mcp_server_lists_tools_and_returns_widget(self) -> None:
        process = subprocess.Popen(
            [sys.executable, str(MCP_SCRIPT)], text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**__import__("os").environ, "CONTEXT_TREE_HOME": str(self.store)},
        )
        try:
            assert process.stdin and process.stdout

            def rpc(request_id: int, method: str, params: dict | None = None) -> dict:
                message = {"jsonrpc": "2.0", "id": request_id, "method": method}
                if params is not None:
                    message["params"] = params
                process.stdin.write(json.dumps(message) + "\n")
                process.stdin.flush()
                return json.loads(process.stdout.readline())

            initialized = rpc(1, "initialize", {"protocolVersion": "2025-06-18"})
            self.assertEqual(initialized["result"]["serverInfo"]["name"], "context-tree")
            tools = rpc(2, "tools/list")["result"]["tools"]
            self.assertTrue(any(tool["name"] == "render_context_tree" for tool in tools))
            self.assertTrue(any(tool["name"] == "context_tree_import_session" for tool in tools))
            resource = rpc(3, "resources/read", {"uri": "ui://context-tree/widget-v1.html"})
            contents = resource["result"]["contents"][0]
            self.assertEqual(contents["mimeType"], "text/html;profile=mcp-app")
            self.assertIn("Context Tree 面板", contents["text"])
            rendered = rpc(4, "tools/call", {"name": "render_context_tree", "arguments": {}})
            self.assertIn("summary", rendered["result"]["structuredContent"])
        finally:
            process.terminate()
            process.wait(timeout=5)
            if process.stdin:
                process.stdin.close()
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()


if __name__ == "__main__":
    unittest.main()

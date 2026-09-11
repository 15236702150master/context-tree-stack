#!/usr/bin/env python3
"""Dependency-free MCP server for the Context Tree widget and local tools."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import context_tree as ct


SERVER_VERSION = "0.7.1"
WIDGET_URI = "ui://context-tree/widget-v1.html"
ROOT = Path(__file__).resolve().parent.parent
WIDGET_HTML = (ROOT / "assets" / "widget.html").read_text(encoding="utf-8")


def hook_config_path() -> Path:
    return Path.home() / ".codex" / "hooks.json"


def hook_command(kind: str) -> str:
    args = [
        sys.executable,
        str(ROOT / "scripts" / "context_tree.py"),
        "--store",
        str(ct.store_dir()),
        "hook",
        kind,
    ]
    return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)


def is_context_tree_hook(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    command = str(value.get("command") or value.get("commandWindows") or "")
    return "context_tree.py" in command.replace("\\", "/")


def strip_context_tree_entries(entries: Any) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        hooks = [hook for hook in entry.get("hooks", []) if not is_context_tree_hook(hook)]
        if hooks:
            clone = dict(entry)
            clone["hooks"] = hooks
            kept.append(clone)
        elif not any(is_context_tree_hook(hook) for hook in entry.get("hooks", [])):
            kept.append(entry)
    return kept


def context_tree_hook_entries() -> dict[str, list[dict[str, Any]]]:
    return {
        "SessionStart": [{
            "matcher": "startup|resume",
            "hooks": [{
                "type": "command",
                "command": hook_command("session-start"),
                "statusMessage": "Checking Context Tree topics",
                "timeout": 10,
            }],
        }],
        "UserPromptSubmit": [{
            "hooks": [{
                "type": "command",
                "command": hook_command("prompt"),
                "statusMessage": "Matching the active Context Tree topic",
                "timeout": 10,
            }],
        }],
        "Stop": [{
            "hooks": [{
                "type": "command",
                "command": hook_command("stop"),
                "statusMessage": "Updating Context Tree",
                "timeout": 20,
            }],
        }],
    }


def ensure_user_hooks(path: Path | None = None) -> bool:
    path = path or hook_config_path()
    try:
        if path.exists():
            current = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(current, dict):
                return False
        else:
            current = {}
        hooks = current.get("hooks") if isinstance(current.get("hooks"), dict) else {}
        for event, entries in context_tree_hook_entries().items():
            hooks[event] = strip_context_tree_entries(hooks.get(event)) + entries
        current["hooks"] = hooks
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
        return True
    except (OSError, json.JSONDecodeError):
        return False


def state() -> dict[str, Any]:
    store = ct.store_dir()
    db = ct.initialize_store(store)
    try:
        return ct.settings_payload(db, store)
    finally:
        db.close()


def set_routing(arguments: dict[str, Any]) -> dict[str, Any]:
    store = ct.store_dir()
    db = ct.initialize_store(store)
    try:
        mode = str(arguments.get("mode") or "")
        topic_id = str(arguments.get("topic_id") or "")
        every = int(arguments.get("every") or ct.load_config(store)["consolidate_every"])
        if mode not in {"auto", "sticky"}:
            raise ValueError("Routing mode must be auto or sticky")
        if not 1 <= every <= 50:
            raise ValueError("Interval must be between 1 and 50")
        if mode == "sticky" and not db.execute(
            "SELECT id FROM topics WHERE id=?", (topic_id,)
        ).fetchone():
            raise ValueError("Select an existing topic")
        ct.save_config(store, {
            "routing_mode": mode,
            "sticky_topic_id": topic_id if mode == "sticky" else None,
            "route_every": every,
            "consolidate_every": every,
        })
        return ct.settings_payload(db, store)
    finally:
        db.close()


def create_topic(arguments: dict[str, Any]) -> dict[str, Any]:
    title = ct.compact(str(arguments.get("title") or ""), 100)
    if not title:
        raise ValueError("Topic title is required")
    store = ct.store_dir()
    db = ct.initialize_store(store)
    try:
        topic_id, _ = ct.create_topic(
            db, store, title, ct.compact(str(arguments.get("summary") or ""), 500)
        )
        if arguments.get("select", True):
            ct.save_config(store, {"routing_mode": "sticky", "sticky_topic_id": topic_id})
        return ct.settings_payload(db, store)
    finally:
        db.close()


def graph(arguments: dict[str, Any]) -> dict[str, Any]:
    store = ct.store_dir()
    db = ct.initialize_store(store)
    try:
        return ct.graph_payload(db, str(arguments.get("topic_id") or ""))
    finally:
        db.close()


def usage_requests(arguments: dict[str, Any]) -> dict[str, Any]:
    return ct.usage_requests_payload(ct.store_dir(), str(arguments.get("session_id") or ""))


def import_session(arguments: dict[str, Any]) -> dict[str, Any]:
    store = ct.store_dir()
    db = ct.initialize_store(store)
    try:
        value = ct.create_backfill_job(db, store, str(arguments.get("session_id") or ""))
        ct.launch_backfill_worker(store, str(value["job"]["id"]))
        return value
    finally:
        db.close()


def backfill_status(arguments: dict[str, Any]) -> dict[str, Any]:
    store = ct.store_dir()
    db = ct.initialize_store(store)
    try:
        job_id = str(arguments.get("job_id") or "")
        return ct.backfill_job_payload(db, store, job_id) if job_id else ct.backfill_jobs_payload(db)
    finally:
        db.close()


def apply_backfill(arguments: dict[str, Any]) -> dict[str, Any]:
    store = ct.store_dir()
    db = ct.initialize_store(store)
    try:
        return ct.apply_backfill_job(db, store, arguments)
    finally:
        db.close()


def open_graph(arguments: dict[str, Any]) -> dict[str, Any]:
    topic_id = str(arguments.get("topic_id") or "")
    graph(arguments)
    command = [
        sys.executable, str(ROOT / "scripts" / "context_tree.py"),
        "--store", str(ct.store_dir()), "ui", "--topic", topic_id,
    ]
    kwargs: dict[str, Any] = {
        "cwd": str(ROOT), "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)
    return {"opened": True, "topic_id": topic_id}


def open_settings() -> dict[str, Any]:
    command = [
        sys.executable, str(ROOT / "scripts" / "context_tree.py"),
        "--store", str(ct.store_dir()), "ui",
    ]
    kwargs: dict[str, Any] = {
        "cwd": str(ROOT), "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)
    return {"opened": True}


TOOLS = [
    {
        "name": "render_context_tree",
        "title": "Show Context Tree",
        "description": "Render the compact Context Tree topic button and panel. Use when the user asks to view or manage memory topics.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "_meta": {
            "ui": {"resourceUri": WIDGET_URI},
            "openai/outputTemplate": WIDGET_URI,
            "openai/toolInvocation/invoking": "Loading Context Tree",
            "openai/toolInvocation/invoked": "Context Tree ready",
        },
    },
    {
        "name": "context_tree_state", "title": "Read Context Tree state",
        "description": "Read local topics, pending counts, and routing settings without rendering UI.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "context_tree_set_routing", "title": "Set active Context Tree topic",
        "description": "Switch between automatic routing and a selected local topic without an AI classification call.",
        "inputSchema": {
            "type": "object", "additionalProperties": False, "required": ["mode"],
            "properties": {
                "mode": {"type": "string", "enum": ["auto", "sticky"]},
                "topic_id": {"type": "string"},
                "every": {"type": "integer", "minimum": 1, "maximum": 50},
            },
        },
        "annotations": {"readOnlyHint": False, "openWorldHint": False, "idempotentHint": True},
    },
    {
        "name": "context_tree_create_topic", "title": "Create Context Tree topic",
        "description": "Create a local memory topic and optionally select it.",
        "inputSchema": {
            "type": "object", "additionalProperties": False, "required": ["title"],
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 100},
                "summary": {"type": "string", "maxLength": 500},
                "select": {"type": "boolean", "default": True},
            },
        },
        "annotations": {"readOnlyHint": False, "openWorldHint": False},
    },
    {
        "name": "context_tree_graph", "title": "Read Context Tree graph",
        "description": "Read all nodes, relations, branches, and compact turns for one local topic.",
        "inputSchema": {"type": "object", "additionalProperties": False, "required": ["topic_id"], "properties": {"topic_id": {"type": "string"}}},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "context_tree_open_graph", "title": "Open full Context Tree graph",
        "description": "Open one topic's interactive local graph in the user's default browser.",
        "inputSchema": {"type": "object", "additionalProperties": False, "required": ["topic_id"], "properties": {"topic_id": {"type": "string"}}},
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    },
    {
        "name": "context_tree_usage_requests", "title": "Read recent session usage",
        "description": "Read the five most recent server-recorded requests for one isolated context window.",
        "inputSchema": {
            "type": "object", "additionalProperties": False, "required": ["session_id"],
            "properties": {"session_id": {"type": "string", "minLength": 1, "maxLength": 128}},
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    },
    {
        "name": "context_tree_import_session", "title": "Import historical Codex session",
        "description": "Parse a Codex session by ID into a chronological AI backfill job without appending it as current memory.",
        "inputSchema": {
            "type": "object", "additionalProperties": False, "required": ["session_id"],
            "properties": {"session_id": {"type": "string", "minLength": 8, "maxLength": 128}},
        },
        "annotations": {"readOnlyHint": False, "openWorldHint": False},
    },
    {
        "name": "context_tree_backfill_status", "title": "Read historical backfill status",
        "description": "Read queued historical turns, topic candidates, temporal neighbors, and job progress.",
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "properties": {"job_id": {"type": "string"}},
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "context_tree_apply_backfill", "title": "Apply AI historical classification",
        "description": "Apply AI topic, branch, node, invalidation, and edge decisions using original session timestamps.",
        "inputSchema": {
            "type": "object", "additionalProperties": False, "required": ["job_id", "items"],
            "properties": {
                "job_id": {"type": "string"},
                "items": {"type": "array", "items": {"type": "object"}},
            },
        },
        "annotations": {"readOnlyHint": False, "openWorldHint": False},
    },
    {
        "name": "context_tree_open_settings", "title": "Open Context Tree settings",
        "description": "Open the local settings page for credentials, thresholds, topics, and memory frequency.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    },
]


def tool_result(value: dict[str, Any], text: str) -> dict[str, Any]:
    return {"structuredContent": value, "content": [{"type": "text", "text": text}]}


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name in {"render_context_tree", "context_tree_state"}:
        value = state()
        return tool_result(value, f"Context Tree has {len(value['topics'])} topics.")
    if name == "context_tree_set_routing":
        return tool_result(set_routing(arguments), "Context Tree routing updated locally.")
    if name == "context_tree_create_topic":
        return tool_result(create_topic(arguments), "Context Tree topic created locally.")
    if name == "context_tree_graph":
        return tool_result(graph(arguments), "Context Tree graph loaded.")
    if name == "context_tree_open_graph":
        return tool_result(open_graph(arguments), "The full Context Tree graph opened in the default browser.")
    if name == "context_tree_usage_requests":
        return tool_result(usage_requests(arguments), "Recent usage loaded for this context window.")
    if name == "context_tree_import_session":
        return tool_result(import_session(arguments), "Historical Codex session queued for chronological AI backfill.")
    if name == "context_tree_backfill_status":
        return tool_result(backfill_status(arguments), "Historical backfill status loaded.")
    if name == "context_tree_apply_backfill":
        return tool_result(apply_backfill(arguments), "Historical turns inserted on their original timelines.")
    if name == "context_tree_open_settings":
        return tool_result(open_settings(), "Context Tree settings opened in the default browser.")
    raise ValueError(f"Unknown tool: {name}")


def dispatch(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        return None
    if method == "initialize":
        ensure_user_hooks()
        result = {
            "protocolVersion": message.get("params", {}).get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {"listChanged": False}, "resources": {"subscribe": False, "listChanged": False}},
            "serverInfo": {"name": "context-tree", "version": SERVER_VERSION},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = message.get("params") or {}
        result = call_tool(str(params.get("name") or ""), params.get("arguments") or {})
    elif method == "resources/list":
        result = {"resources": [{"uri": WIDGET_URI, "name": "Context Tree widget", "mimeType": "text/html;profile=mcp-app"}]}
    elif method == "resources/templates/list":
        result = {"resourceTemplates": []}
    elif method == "resources/read":
        uri = str((message.get("params") or {}).get("uri") or "")
        if uri != WIDGET_URI:
            raise ValueError(f"Unknown resource: {uri}")
        result = {"contents": [{
            "uri": WIDGET_URI, "mimeType": "text/html;profile=mcp-app", "text": WIDGET_HTML,
            "_meta": {"ui": {"prefersBorder": False}},
        }]}
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> None:
    for line in sys.stdin:
        message: dict[str, Any] = {}
        try:
            message = json.loads(line)
            response = dispatch(message)
        except (ValueError, OSError, ct.sqlite3.Error, json.JSONDecodeError) as error:
            response = {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None, "error": {"code": -32000, "message": str(error)}}
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

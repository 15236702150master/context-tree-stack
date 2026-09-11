#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse, parse_qs


HOST = os.environ.get("USAGE_SIDECAR_HOST", "127.0.0.1")
PORT = int(os.environ.get("USAGE_SIDECAR_PORT", "8098"))
MAX_SESSION_ID_LENGTH = 128
DEFAULT_SESSION_LIMIT = 50
DEFAULT_REQUEST_LIMIT = 5
MAX_SESSION_LIMIT = 100
MAX_REQUEST_LIMIT = 10000


def clamp_limit(raw: str | None, fallback: int, max_value: int) -> int:
    try:
        value = int((raw or "").strip())
    except ValueError:
        value = fallback
    if value <= 0:
        return fallback
    return min(value, max_value)


def psql_json(sql: str, variables: dict[str, str], timeout: float = 5.0) -> object:
    env = os.environ.copy()
    password = env.get("DATABASE_PASSWORD", "")
    if password:
        env["PGPASSWORD"] = password
    args = [
        "psql",
        "-X",
        "-q",
        "-A",
        "-t",
        "-v",
        "ON_ERROR_STOP=1",
        "-h",
        env.get("DATABASE_HOST", "127.0.0.1"),
        "-p",
        env.get("DATABASE_PORT", "5432"),
        "-U",
        env.get("DATABASE_USER", "sub2api"),
        "-d",
        env.get("DATABASE_DBNAME", "sub2api"),
    ]
    for key, value in variables.items():
        args.extend(["-v", f"{key}={value}"])
    completed = subprocess.run(
        args,
        input=sql,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "psql failed")
    payload = completed.stdout.strip()
    if not payload:
        return None
    return json.loads(payload)


def extract_api_key(headers) -> str:
    authorization = headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return headers.get("x-api-key", "").strip()


def authenticate(api_key: str) -> dict[str, int] | None:
    if not api_key or len(api_key) > 512:
        return None
    value = psql_json(
        """
        SELECT row_to_json(auth)
        FROM (
            SELECT ak.id AS api_key_id, ak.user_id AS user_id
            FROM api_keys ak
            JOIN users u ON u.id = ak.user_id
            WHERE ak.key = :'api_key'
              AND ak.deleted_at IS NULL
              AND ak.status = 'active'
              AND u.deleted_at IS NULL
              AND u.status = 'active'
              AND (ak.expires_at IS NULL OR ak.expires_at > now())
            LIMIT 1
        ) auth
        """,
        {"api_key": api_key},
    )
    return value if isinstance(value, dict) else None


def sessions_payload(auth: dict[str, int], limit: int) -> object:
    return psql_json(
        """
        WITH latest AS (
            SELECT DISTINCT ON (session_id)
                session_id, thread_id, window_slot, session_id_source,
                request_id, COALESCE(NULLIF(requested_model, ''), model) AS model,
                COALESCE(input_tokens, 0) AS input_tokens,
                COALESCE(cache_creation_tokens, 0) AS cache_creation_tokens,
                COALESCE(cache_read_tokens, 0) AS cache_read_tokens,
                first_token_ms, duration_ms, COALESCE(actual_cost, total_cost, 0) AS actual_cost,
                created_at, id
            FROM usage_logs
            WHERE user_id = :user_id AND api_key_id = :api_key_id AND session_id IS NOT NULL
            ORDER BY session_id, created_at DESC, id DESC
        ), totals AS (
            SELECT session_id, COUNT(*) AS request_count, COALESCE(SUM(COALESCE(actual_cost, total_cost, 0)), 0) AS total_cost
            FROM usage_logs
            WHERE user_id = :user_id AND api_key_id = :api_key_id AND session_id IS NOT NULL
            GROUP BY session_id
        ), shaped AS (
            SELECT latest.session_id, latest.thread_id, latest.window_slot, latest.session_id_source,
                latest.request_id AS latest_request_id, latest.model,
                (latest.input_tokens + latest.cache_creation_tokens + latest.cache_read_tokens)::bigint AS context_tokens,
                latest.first_token_ms, latest.duration_ms, latest.actual_cost AS current_cost,
                COALESCE((
                    SELECT SUM(recent.actual_cost) FROM (
                        SELECT COALESCE(ul.actual_cost, ul.total_cost, 0) AS actual_cost
                        FROM usage_logs ul
                        WHERE ul.user_id = :user_id AND ul.api_key_id = :api_key_id AND ul.session_id = latest.session_id
                        ORDER BY ul.created_at DESC, ul.id DESC LIMIT 5
                    ) recent
                ), 0) AS recent_cost,
                totals.total_cost, totals.request_count, latest.created_at AS last_active_at, latest.id
            FROM latest
            JOIN totals ON totals.session_id = latest.session_id
            ORDER BY latest.created_at DESC, latest.id DESC
            LIMIT :limit
        )
        SELECT json_build_object(
            'mode', 'real',
            'sessions', COALESCE(json_agg(row_to_json(shaped)), '[]'::json)
        )
        FROM shaped
        """,
        {"user_id": str(auth["user_id"]), "api_key_id": str(auth["api_key_id"]), "limit": str(limit)},
    )


def requests_payload(auth: dict[str, int], field: str, value: str, limit: int) -> object:
    if field not in {"session_id", "thread_id"}:
        raise ValueError("invalid field")
    label = "session_id" if field == "session_id" else "thread_id"
    return psql_json(
        f"""
        WITH shaped AS (
            SELECT request_id, COALESCE(NULLIF(requested_model, ''), model) AS model,
                COALESCE(reasoning_effort, '') AS reasoning_effort,
                COALESCE(service_tier, '') AS service_tier,
                COALESCE(input_tokens, 0) AS input_tokens,
                COALESCE(output_tokens, 0) AS output_tokens,
                COALESCE(cache_creation_tokens, 0) AS cache_creation_tokens,
                COALESCE(cache_read_tokens, 0) AS cache_read_tokens,
                (COALESCE(input_tokens, 0) + COALESCE(cache_creation_tokens, 0) + COALESCE(cache_read_tokens, 0))::bigint AS context_tokens,
                first_token_ms, duration_ms, COALESCE(actual_cost, total_cost, 0) AS cost,
                CASE WHEN COALESCE(total_cost, 0) > 0 THEN
                    (COALESCE(input_cost, 0) + COALESCE(cache_creation_cost, 0) + COALESCE(cache_read_cost, 0))
                    * COALESCE(actual_cost, total_cost, 0) / total_cost
                ELSE 0 END AS context_cost,
                created_at
            FROM usage_logs
            WHERE user_id = :user_id AND api_key_id = :api_key_id AND {field} = :'lookup_value'
            ORDER BY created_at DESC, id DESC
            LIMIT :limit
        )
        SELECT json_build_object(
            'mode', 'real',
            '{label}', :'lookup_value',
            'requests', COALESCE(json_agg(row_to_json(shaped)), '[]'::json)
        )
        FROM shaped
        """,
        {
            "user_id": str(auth["user_id"]),
            "api_key_id": str(auth["api_key_id"]),
            "lookup_value": value,
            "limit": str(limit),
        },
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "context-tree-usage-sidecar/1"

    def log_message(self, fmt: str, *args) -> None:
        return

    def send_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("X-Context-Tree-Usage-Sidecar", "1")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/health":
            self.send_json(200, {"status": "ok"})
            return

        try:
            api_key = extract_api_key(self.headers)
            auth = authenticate(api_key)
            if not auth:
                self.send_json(401, {"error": {"type": "authentication_error", "message": "Invalid API key"}})
                return

            if path == "/v1/sub2api/usage/sessions":
                limit = clamp_limit(query.get("limit", [""])[0], DEFAULT_SESSION_LIMIT, MAX_SESSION_LIMIT)
                self.send_json(200, sessions_payload(auth, limit) or {"mode": "real", "sessions": []})
                return
            match = re.fullmatch(r"/v1/sub2api/usage/sessions/([^/]+)/requests", path)
            if match:
                session_id = unquote(match.group(1))
                if not session_id or len(session_id) > MAX_SESSION_ID_LENGTH:
                    self.send_json(400, {"error": {"type": "invalid_request_error", "message": "invalid session_id"}})
                    return
                limit = clamp_limit(query.get("limit", [""])[0], DEFAULT_REQUEST_LIMIT, MAX_REQUEST_LIMIT)
                self.send_json(200, requests_payload(auth, "session_id", session_id, limit))
                return
            match = re.fullmatch(r"/v1/sub2api/usage/threads/([^/]+)/requests", path)
            if match:
                thread_id = unquote(match.group(1))
                if not thread_id or len(thread_id) > MAX_SESSION_ID_LENGTH:
                    self.send_json(400, {"error": {"type": "invalid_request_error", "message": "invalid thread_id"}})
                    return
                limit = clamp_limit(query.get("limit", [""])[0], DEFAULT_REQUEST_LIMIT, MAX_REQUEST_LIMIT)
                self.send_json(200, requests_payload(auth, "thread_id", thread_id, limit))
                return
            self.send_json(404, {"error": {"type": "not_found_error", "message": "Not found"}})
        except Exception:
            self.send_json(500, {"error": {"type": "api_error", "message": "Usage telemetry is unavailable"}})


if __name__ == "__main__":
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()

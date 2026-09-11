# Usage Sidecar

The sidecar is an optional, read-only HTTP adapter for Context Tree. It
authenticates an API key against an existing PostgreSQL-backed Sub2API
deployment and returns the request history needed for server-side cost and
latency cards. It does not create or migrate tables.

## Requirements

- Python 3.10 or newer
- The PostgreSQL `psql` client on `PATH`
- Existing `users`, `api_keys`, and `usage_logs` tables with the columns used
  by `server.py`

Copy `.env.example` to a private environment file and set the database values.
The password is passed to `psql` through `PGPASSWORD`; keep the environment
file readable only by the service account.

## Run locally

```powershell
$env:DATABASE_PASSWORD = "<database-password>"
python .\sidecar\server.py
```

The default listener is `127.0.0.1:8098`. Check it without credentials:

```powershell
Invoke-RestMethod http://127.0.0.1:8098/health
```

## API

`GET /health` is unauthenticated. All usage routes accept either
`Authorization: Bearer <API_KEY>` or `x-api-key: <API_KEY>`:

| Route | Purpose |
| --- | --- |
| `/v1/sub2api/usage/sessions` | Recent sessions (`limit` default 50, max 100) |
| `/v1/sub2api/usage/sessions/{session_id}/requests` | Requests for one session (`limit` default 5, max 10,000) |
| `/v1/sub2api/usage/threads/{thread_id}/requests` | Requests for one thread (`limit` default 5, max 10,000) |

IDs must be URL-encoded and are limited to 128 characters. Errors use the
following shape:

```json
{"error": {"type": "authentication_error", "message": "Invalid API key"}}
```

## Nginx and systemd

`nginx.locations.conf` contains only the three proxy locations. Include it in
the server block that owns your public API host, then terminate TLS at Nginx.
The sample unit assumes:

- code at `/opt/context-tree-usage-sidecar/server.py`
- environment at `/etc/context-tree/context-tree.env`

Adjust those paths, the service user, and the public server block for your
deployment. The helper below is idempotent and creates a timestamped backup:

```bash
sudo python3 sidecar/install_nginx_include.py \
  --nginx-conf /etc/nginx/conf.d/context-tree.conf \
  --include-path /etc/nginx/snippets/context-tree-usage-sidecar.locations.conf
sudo nginx -t
sudo systemctl enable --now context-tree-usage-sidecar.service
```

Use `--dry-run` to inspect the proposed edit first. Keep the sidecar on
loopback and expose it only through HTTPS; grant the database account the
minimum read permissions required by the queries.

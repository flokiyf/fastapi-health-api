from __future__ import annotations

from workers import DurableObject, WorkerEntrypoint


class AuditLog(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.sql = ctx.storage.sql
        self.sql.exec(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL,
                method TEXT NOT NULL,
                source TEXT,
                client_id TEXT,
                session_id TEXT,
                request_id TEXT,
                headers_json TEXT,
                request_json TEXT NOT NULL,
                response_json TEXT,
                error TEXT,
                duration_ms REAL
            );
            CREATE INDEX IF NOT EXISTS audit_events_created_at
                ON audit_events(created_at DESC);
            CREATE INDEX IF NOT EXISTS audit_events_method
                ON audit_events(method);
            """
        )

    async def create_event(self, event):
        from audit import encode

        self.sql.exec(
            """
            INSERT INTO audit_events (
                id, created_at, completed_at, status, method, source, client_id,
                session_id, request_id, headers_json, request_json, response_json,
                error, duration_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            event["id"],
            event["created_at"],
            event.get("completed_at"),
            event["status"],
            event["method"],
            event.get("source"),
            event.get("client_id"),
            event.get("session_id"),
            event.get("request_id"),
            encode(event.get("headers_json")),
            encode(event.get("request_json")),
            event.get("response_json"),
            event.get("error"),
            event.get("duration_ms"),
        )

    async def finish_event(self, event_id, completion):
        self.sql.exec(
            """
            UPDATE audit_events
            SET completed_at = ?, status = ?, response_json = ?, error = ?, duration_ms = ?
            WHERE id = ?
            """,
            completion["completed_at"],
            completion["status"],
            completion.get("response_json"),
            completion.get("error"),
            completion["duration_ms"],
            event_id,
        )

    async def list_events(self, limit=50, method=None, status=None):
        query = "SELECT * FROM audit_events"
        clauses = []
        bindings = []
        if method:
            clauses.append("method = ?")
            bindings.append(method)
        if status:
            clauses.append("status = ?")
            bindings.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        bindings.append(max(1, min(int(limit), 200)))
        rows = self.sql.exec(query, *bindings).toArray()
        return [row.to_py() if hasattr(row, "to_py") else dict(row) for row in rows]

    async def get_event(self, event_id):
        rows = self.sql.exec(
            "SELECT * FROM audit_events WHERE id = ? LIMIT 1", event_id
        ).toArray()
        if not rows:
            return None
        row = rows[0]
        return row.to_py() if hasattr(row, "to_py") else dict(row)


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        # FastMCP imports Rich, which uses secure randomness. Cloudflare only permits
        # that API while a request context is active, so the import must stay lazy.
        from worker_app import fetch

        return await fetch(request, self.env)

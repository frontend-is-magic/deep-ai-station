"""Immutable intent, owner-scoped approval, and one publication per operation."""

import hashlib
import json
import math

from auth import refresh, require
from errors import LabError

OP_FIELDS = (
    "operation_id",
    "owner_id",
    "requester_id",
    "tool",
    "document_id",
    "expected_version",
    "before_content",
    "content",
    "intent_hash",
    "status",
    "prepared_at",
    "approved_at",
    "expires_at",
)
RECEIPT_FIELDS = ("operation_id", "document_id", "version", "content", "intent_hash", "applied_at")


def intent(principal, payload):
    business = {
        "owner_id": principal.owner_id,
        "requester_id": principal.requester_id,
        "tool": payload["tool"],
        **payload["arguments"],
    }
    encoded = json.dumps(
        business, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def operation_row(connection, owner, operation_id):
    row = connection.execute(
        "SELECT * FROM operations WHERE owner_id=? AND operation_id=?", (owner, operation_id)
    ).fetchone()
    if row is None:
        raise LabError(404, "operation_not_found")
    return row


def document_row(connection, owner, document_id):
    row = connection.execute(
        "SELECT * FROM documents WHERE owner_id=? AND id=?", (owner, document_id)
    ).fetchone()
    if row is None:
        raise LabError(404, "document_not_found")
    return row


def operation_value(connection, row):
    receipt = connection.execute(
        "SELECT * FROM publications WHERE owner_id=? AND operation_id=?",
        (row["owner_id"], row["operation_id"]),
    ).fetchone()
    return {
        **{key: row[key] for key in OP_FIELDS},
        "receipt": {key: receipt[key] for key in RECEIPT_FIELDS} if receipt else None,
    }


def expire(connection, row, now):
    if row["status"] == "approved" and now >= row["expires_at"]:
        connection.execute(
            "UPDATE operations SET status='expired' WHERE owner_id=? AND operation_id=?",
            (row["owner_id"], row["operation_id"]),
        )
        row = operation_row(connection, row["owner_id"], row["operation_id"])
    return row


def reject_terminal(row):
    if row["status"] == "expired":
        raise LabError(409, "operation_expired", persist=True)
    if row["status"] == "revoked":
        raise LabError(409, "operation_revoked")


class Service:
    def __init__(self, repository, sessions, *, approval_seconds=60):
        if (
            type(approval_seconds) not in (int, float)
            or not math.isfinite(approval_seconds)
            or approval_seconds <= 0
        ):
            raise ValueError("invalid approval duration")
        self.repository = repository
        self.sessions = sessions
        self.approval_seconds = approval_seconds

    def call(self, auth, action, operation_id=None, payload=None):
        def work(connection, principal, now):
            owner = principal.owner_id
            if action == "documents":
                require(principal, "read")
                rows = connection.execute(
                    "SELECT id,title,content,version FROM documents WHERE owner_id=? ORDER BY id",
                    (owner,),
                ).fetchall()
                return {"items": [dict(row) for row in rows]}
            if action == "document":
                row = document_row(connection, owner, operation_id)
                require(principal, "read")
                return {key: row[key] for key in ("id", "title", "content", "version")}
            if action == "prepare":
                existing = connection.execute(
                    "SELECT * FROM operations WHERE owner_id=? AND operation_id=?",
                    (owner, payload["operation_id"]),
                ).fetchone()
                digest = intent(principal, payload)
                if existing is not None:
                    require(principal, "prepare")
                    if (
                        existing["intent_hash"] != digest
                        or existing["requester_id"] != principal.requester_id
                    ):
                        raise LabError(409, "operation_conflict")
                    return operation_value(connection, expire(connection, existing, now))
                arguments = payload["arguments"]
                document = document_row(connection, owner, arguments["document_id"])
                require(principal, "prepare")
                if document["version"] != arguments["expected_version"]:
                    raise LabError(409, "stale_document")
                connection.execute(
                    """INSERT INTO operations(owner_id,operation_id,requester_id,tool,document_id,
                       expected_version,before_content,content,intent_hash,status,prepared_at)
                       VALUES(?,?,?,?,?,?,?,?,?,'prepared',?)""",
                    (
                        owner,
                        payload["operation_id"],
                        principal.requester_id,
                        payload["tool"],
                        arguments["document_id"],
                        arguments["expected_version"],
                        document["content"],
                        arguments["content"],
                        digest,
                        now,
                    ),
                )
                return operation_value(
                    connection, operation_row(connection, owner, payload["operation_id"])
                )
            row = operation_row(connection, owner, operation_id)
            require(
                principal,
                "read"
                if action == "operation"
                else "execute"
                if action == "execute"
                else "approve",
            )
            if action == "operation":
                return operation_value(connection, expire(connection, row, now))
            if row["intent_hash"] != payload["intent_hash"]:
                raise LabError(409, "intent_mismatch")
            if action == "execute" and row["requester_id"] != principal.requester_id:
                raise LabError(403, "requester_mismatch")
            row = expire(connection, row, now)
            if action == "execute" and row["status"] == "applied":
                return {"operation": operation_value(connection, row), "replayed": True}
            if row["status"] == "applied":
                raise LabError(409, "already_applied")
            if action == "revoke" and row["status"] == "revoked":
                return operation_value(connection, row)
            reject_terminal(row)
            if action == "revoke":
                connection.execute(
                    "UPDATE operations SET status='revoked' WHERE owner_id=? AND operation_id=?",
                    (owner, operation_id),
                )
            elif action == "approve":
                if row["status"] == "approved":
                    return operation_value(connection, row)
                document = document_row(connection, owner, row["document_id"])
                if document["version"] != row["expected_version"]:
                    raise LabError(409, "stale_document")
                expires_at = now + self.approval_seconds
                if not math.isfinite(expires_at) or expires_at <= now:
                    raise LabError(503, "repository_unavailable")
                connection.execute(
                    "UPDATE operations SET status='approved',approved_at=?,expires_at=? WHERE owner_id=? AND operation_id=?",
                    (now, expires_at, owner, operation_id),
                )
            elif action == "execute":
                if row["status"] != "approved":
                    raise LabError(403, "approval_required")
                changed = connection.execute(
                    "UPDATE documents SET content=?,version=version+1 WHERE owner_id=? AND id=? AND version=?",
                    (row["content"], owner, row["document_id"], row["expected_version"]),
                )
                if changed.rowcount != 1:
                    raise LabError(409, "stale_document")
                self.repository.fault_hook("after_document_update", connection)
                connection.execute(
                    "INSERT INTO publications(owner_id,operation_id,document_id,version,content,intent_hash,applied_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        owner,
                        operation_id,
                        row["document_id"],
                        row["expected_version"] + 1,
                        row["content"],
                        row["intent_hash"],
                        now,
                    ),
                )
                self.repository.fault_hook("after_publication_insert", connection)
                connection.execute(
                    "UPDATE operations SET status='applied' WHERE owner_id=? AND operation_id=?",
                    (owner, operation_id),
                )
                return {
                    "operation": operation_value(
                        connection, operation_row(connection, owner, operation_id)
                    ),
                    "replayed": False,
                }
            else:
                raise ValueError("unknown action")
            return operation_value(connection, operation_row(connection, owner, operation_id))

        try:
            return self.repository.transaction(work, lambda: refresh(auth, self.sessions))
        except LabError:
            raise
        except Exception:
            raise LabError(503, "repository_unavailable") from None

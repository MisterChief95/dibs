"""Local, cooperative task coordination. Run with --help; no third-party packages."""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

VERSION = 3
EXIT = {"internal": 1, "conflict": 2, "invalid_input": 3, "storage": 4, "not_found": 5}
SKILL_DIR = Path(__file__).resolve().parents[1]
# os.path.isreserved is 3.13+; PureWindowsPath.is_reserved covers older runtimes.
is_reserved = getattr(
    os.path, "isreserved", lambda part: PureWindowsPath(part).is_reserved()
)
STATUSES = ("todo", "in_progress", "blocked", "review", "done", "cancelled")
REQUIRED_SPEC_KEYS = {
    "id",
    "title",
    "priority",
    "depends_on",
    "work_areas",
    "description",
    "acceptance",
}
OPTIONAL_SPEC_KEYS = {"type", "tags"}
MIGRATIONS = [
    [
        "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        """CREATE TABLE tasks (
            id TEXT PRIMARY KEY, spec TEXT NOT NULL, priority TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'todo'
                CHECK(status IN ('todo','in_progress','blocked','review','done','cancelled')),
            revision INTEGER NOT NULL DEFAULT 0, owner TEXT, token TEXT, expires REAL,
            created REAL NOT NULL, updated REAL NOT NULL)""",
        """CREATE TABLE dependencies (task_id TEXT REFERENCES tasks(id),
            requires_task_id TEXT REFERENCES tasks(id), PRIMARY KEY(task_id,requires_task_id))""",
        """CREATE TABLE reservations (path TEXT NOT NULL, scope TEXT NOT NULL,
            task_id TEXT NOT NULL REFERENCES tasks(id), token TEXT NOT NULL, expires REAL NOT NULL,
            PRIMARY KEY(path,scope,task_id))""",
        """CREATE TABLE events (sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT REFERENCES tasks(id), actor TEXT NOT NULL, type TEXT NOT NULL,
            data TEXT NOT NULL, timestamp REAL NOT NULL)""",
        """CREATE TABLE handoffs (id INTEGER PRIMARY KEY, task_id TEXT REFERENCES tasks(id),
            actor TEXT NOT NULL, data TEXT NOT NULL, timestamp REAL NOT NULL)""",
    ],
    [
        "CREATE INDEX tasks_ready ON tasks(status, priority, id)",
        "CREATE INDEX events_task ON events(task_id, sequence)",
        "CREATE INDEX reservations_task ON reservations(task_id)",
    ],
    [
        "ALTER TABLE tasks ADD COLUMN task_type TEXT NOT NULL DEFAULT 'task'",
        """CREATE TABLE task_tags (task_id TEXT NOT NULL REFERENCES tasks(id),
            tag TEXT NOT NULL, PRIMARY KEY(task_id,tag))""",
        "CREATE INDEX tasks_created ON tasks(created)",
        "CREATE INDEX tasks_updated ON tasks(updated)",
        "CREATE INDEX tasks_type ON tasks(task_type)",
        "CREATE INDEX task_tags_tag ON task_tags(tag,task_id)",
    ],
]


class DibsError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def fail(code, message):
    raise DibsError(code, message)


def encoded(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                fail("invalid_input", f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    text = (
        sys.stdin.buffer.read().decode("utf-8-sig")
        if path == "-"
        else Path(path).read_text(encoding="utf-8-sig")
    )
    return json.loads(
        text,
        object_pairs_hook=unique,
        parse_constant=lambda value: fail("invalid_input", f"Invalid number: {value}"),
    )


def task_order(row):
    prefix, number = row["id"].rsplit("-", 1)
    return row["priority"], prefix, int(number)


def nonempty(value, name):
    if not isinstance(value, str) or not value.strip():
        fail("invalid_input", f"{name} must be nonempty text")


def label(value, name):
    nonempty(value, name)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", value):
        fail(
            "invalid_input",
            f"{name} must use lowercase letters, numbers, dot, underscore, or hyphen",
        )
    return value


def parse_timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (ValueError, OverflowError):
        raise argparse.ArgumentTypeError(
            "use an ISO-8601 timestamp, e.g. 2026-09-14T12:00:00Z"
        )


def validate_spec(spec):
    keys = set(spec) if isinstance(spec, dict) else set()
    if (
        not isinstance(spec, dict)
        or not REQUIRED_SPEC_KEYS <= keys
        or keys - REQUIRED_SPEC_KEYS - OPTIONAL_SPEC_KEYS
    ):
        fail(
            "invalid_input",
            "Task requires fields: "
            + ", ".join(sorted(REQUIRED_SPEC_KEYS))
            + "; optional: "
            + ", ".join(sorted(OPTIONAL_SPEC_KEYS)),
        )
    for field in ("id", "title", "description"):
        nonempty(spec[field], field)
    if not re.fullmatch(r"[A-Z][A-Z0-9]*-\d+", spec["id"]):
        fail("invalid_input", "Task ID must resemble TASK-001")
    if spec["priority"] not in ("P0", "P1", "P2", "P3"):
        fail("invalid_input", "Priority must be P0, P1, P2, or P3")
    for field in ("depends_on", "work_areas", "acceptance"):
        values = spec[field]
        if not isinstance(values, list):
            fail("invalid_input", f"{field} must be an array")
        for value in values:
            nonempty(value, field)
        if len(set(values)) != len(values):
            fail("invalid_input", f"Duplicate {field} entries")
    if not spec["acceptance"]:
        fail("invalid_input", "Acceptance evidence requirements cannot be empty")
    label(spec.get("type", "task"), "type")
    tags = spec.get("tags", [])
    if not isinstance(tags, list):
        fail("invalid_input", "tags must be an array")
    for tag in tags:
        label(tag, "tag")
    if len(set(tags)) != len(tags):
        fail("invalid_input", "Duplicate tags")


def validate_graph(specs):
    # Kahn's algorithm also handles large imports without Python recursion limits.
    pending = {key: set(value["depends_on"]) for key, value in specs.items()}
    for key, deps in pending.items():
        if deps - specs.keys():
            fail(
                "invalid_input",
                f"{key}: unknown dependencies {sorted(deps - specs.keys())}",
            )
    while pending:
        ready = {key for key, deps in pending.items() if not deps}
        if not ready:
            fail("invalid_input", "Dependency cycle: " + ", ".join(sorted(pending)))
        pending = {
            key: deps - ready for key, deps in pending.items() if key not in ready
        }


def wal_safe(version):
    return (
        version >= (3, 51, 3)
        or ((3, 50, 7) <= version < (3, 51, 0))
        or ((3, 44, 6) <= version < (3, 45, 0))
    )


class Store:
    def __init__(self, args):
        self.args = args
        self.root = Path(args.workspace).resolve()
        if not self.root.is_dir():
            fail("invalid_input", "Workspace must be an existing directory")
        self.path = (
            Path(args.db).resolve() if args.db else self.root / ".dibs/tasks.sqlite3"
        )
        if str(self.path).startswith(("\\\\", "//")):
            fail("invalid_input", "Use a local disk, not a network path")
        for label, path in (("Workspace", self.root), ("Database", self.path)):
            if Path(os.path.normcase(path)).is_relative_to(os.path.normcase(SKILL_DIR)):
                fail(
                    "invalid_input",
                    f"{label} cannot be inside the skill directory ({SKILL_DIR})",
                )
        if args.command != "init" and not self.path.is_file():
            fail("not_found", "Database missing; run init first")
        if args.command == "init":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(
            self.path, timeout=args.busy_timeout / 1000, isolation_level=None
        )
        try:
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA synchronous=FULL")
            if args.command != "init":
                self.check_version()
                if self.db.execute("PRAGMA journal_mode").fetchone()[
                    0
                ] == "wal" and not wal_safe(sqlite3.sqlite_version_info):
                    fail(
                        "storage",
                        "This runtime lacks the WAL fix; use a patched runtime",
                    )
        except BaseException:
            self.db.close()
            raise

    def check_version(self):
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version != VERSION:
            fail(
                "storage",
                f"Schema {version}; expected {VERSION}. Run init to migrate older schemas",
            )
        row = self.db.execute(
            "SELECT value FROM metadata WHERE key='workspace'"
        ).fetchone()
        if not row or row[0] != str(self.root).casefold():
            fail("invalid_input", "Database belongs to a different workspace")

    def begin(self):
        for attempt in range(3):
            try:
                self.db.execute("BEGIN IMMEDIATE")
                return
            except sqlite3.OperationalError as error:
                if "locked" not in str(error).lower() or attempt == 2:
                    raise
                time.sleep(0.025 * (attempt + 1))

    def event(self, kind, task=None, data=None):
        self.db.execute(
            "INSERT INTO events(task_id,actor,type,data,timestamp) VALUES (?,?,?,?,?)",
            (task, self.args.actor, kind, encoded(data or {}), time.time()),
        )

    def initialize(self):
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > VERSION:
            fail("storage", f"Schema {version} is newer than this tool")
        if version:
            row = self.db.execute(
                "SELECT value FROM metadata WHERE key='workspace'"
            ).fetchone()
            if not row or row[0] != str(self.root).casefold():
                fail("invalid_input", "Database belongs to a different workspace")
        mode = self.args.journal
        if mode == "wal" and not wal_safe(sqlite3.sqlite_version_info):
            fail(
                "storage",
                f"SQLite {sqlite3.sqlite_version} lacks the WAL fix. Use init --journal delete explicitly",
            )
        current = self.db.execute(f"PRAGMA journal_mode={mode}").fetchone()[0]
        if current != mode:
            fail("storage", f"Could not select journal mode {mode}")
        self.begin()
        # Re-read under the lock: concurrent idempotent init calls must not replay DDL.
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        for number in range(version, VERSION):
            for statement in MIGRATIONS[number]:
                self.db.execute(statement)
            self.db.execute(f"PRAGMA user_version={number + 1}")
        self.db.execute(
            "INSERT OR IGNORE INTO metadata VALUES ('workspace',?)",
            (str(self.root).casefold(),),
        )
        self.db.execute(
            "INSERT OR REPLACE INTO metadata VALUES ('schema_version',?)",
            (str(VERSION),),
        )
        if version != VERSION:
            self.event("migrate", data={"from": version, "to": VERSION})
        self.db.commit()
        self.check_version()
        return {
            "database": str(self.path),
            "schema_version": VERSION,
            "journal": current,
            "sqlite_version": sqlite3.sqlite_version,
        }

    def row(self, task):
        row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task,)).fetchone()
        if row is None:
            fail("not_found", f"Unknown task: {task}")
        return row

    def detail(self, task):
        row = dict(self.row(task))
        row["spec"] = json.loads(row["spec"])
        row["spec"].setdefault("type", row.pop("task_type"))
        row["spec"]["tags"] = [
            r[0]
            for r in self.db.execute(
                "SELECT tag FROM task_tags WHERE task_id=? ORDER BY tag", (task,)
            )
        ]
        row["abandoned"] = bool(row["token"] and row["expires"] <= time.time())
        if row["abandoned"]:
            if row["status"] != "blocked":
                row["revision"] += (
                    1  # Matches the next writer's materialized expiry event.
                )
            row["status"] = "blocked"
        row.pop("token")
        row["reservations"] = [
            dict(r)
            for r in self.db.execute(
                "SELECT path,scope,expires FROM reservations WHERE task_id=? ORDER BY path",
                (task,),
            )
        ]
        row["handoffs"] = [
            dict(r) | {"data": json.loads(r["data"])}
            for r in self.db.execute(
                "SELECT * FROM handoffs WHERE task_id=? ORDER BY id", (task,)
            )
        ]
        return row

    def expire(self):
        for row in self.db.execute(
            "SELECT id FROM tasks WHERE token IS NOT NULL AND expires<=? AND status!='blocked'",
            (time.time(),),
        ).fetchall():
            self.db.execute(
                "UPDATE tasks SET status='blocked',revision=revision+1,updated=? WHERE id=?",
                (time.time(), row["id"]),
            )
            self.event("abandoned", row["id"])

    def ready(self, row):
        return not self.db.execute(
            """SELECT 1 FROM dependencies d JOIN tasks t ON t.id=d.requires_task_id
            WHERE d.task_id=? AND t.status!='done' LIMIT 1""",
            (row["id"],),
        ).fetchone()

    def specs(self):
        return {
            row["id"]: json.loads(row["spec"])
            for row in self.db.execute("SELECT id,spec FROM tasks")
        }

    def replace_tags(self, task, tags):
        self.db.execute("DELETE FROM task_tags WHERE task_id=?", (task,))
        self.db.executemany(
            "INSERT INTO task_tags(task_id,tag) VALUES (?,?)",
            [(task, tag) for tag in tags],
        )

    def task_rows(self, status=None):
        args = self.args
        clauses, values = [], []
        filters = (
            ("status", status or getattr(args, "status", None), "="),
            ("task_type", getattr(args, "task_type", None), "="),
            ("created", getattr(args, "created_after", None), ">="),
            ("created", getattr(args, "created_before", None), "<="),
            ("updated", getattr(args, "updated_after", None), ">="),
            ("updated", getattr(args, "updated_before", None), "<="),
        )
        for column, value, operator in filters:
            if value is not None:
                clauses.append(f"{column}{operator}?")
                values.append(value)
        for tag in getattr(args, "tag", []):
            clauses.append(
                "EXISTS (SELECT 1 FROM task_tags WHERE task_id=tasks.id AND tag=?)"
            )
            values.append(tag)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return self.db.execute("SELECT * FROM tasks" + where, values).fetchall()

    def import_tasks(self):
        payload = read_json(self.args.file)
        if not isinstance(payload, dict) or not isinstance(payload.get("tasks"), list):
            fail("invalid_input", "Import must contain a tasks array")
        specs = self.specs()
        seen, added = set(), []
        for spec in payload["tasks"]:
            validate_spec(spec)
            task = spec["id"]
            if task in seen:
                fail("invalid_input", f"Duplicate task: {task}")
            seen.add(task)
            if task in specs and specs[task] != spec:
                fail("conflict", f"{task} changed; use amend with its revision")
            if task not in specs:
                added.append(task)
            specs[task] = spec
        validate_graph(specs)
        for task in added:
            spec = specs[task]
            now = time.time()
            self.db.execute(
                """INSERT INTO tasks(id,spec,priority,created,updated,task_type)
                VALUES (?,?,?,?,?,?)""",
                (
                    task,
                    encoded(spec),
                    spec["priority"],
                    now,
                    now,
                    spec.get("type", "task"),
                ),
            )
            self.replace_tags(task, spec.get("tags", []))
        for task in added:
            for dep in specs[task]["depends_on"]:
                self.db.execute("INSERT INTO dependencies VALUES (?,?)", (task, dep))
            self.event("import", task)
        digest = hashlib.sha256(encoded(payload).encode()).hexdigest()
        self.db.execute(
            "INSERT OR REPLACE INTO metadata VALUES ('import_hash',?)", (digest,)
        )
        self.db.execute(
            "INSERT OR REPLACE INTO metadata VALUES ('import_source',?)",
            (encoded(payload.get("source", {})),),
        )
        return {
            "added": added,
            "unchanged": sorted(seen - set(added)),
            "import_hash": digest,
        }

    def reservation_keys(self, work_areas=()):
        result = []
        requested = [(value, "file") for value in self.args.reserve_file] + [
            (value, "tree") for value in self.args.reserve_tree
        ]
        # Work areas become tree reservations for existing directories, otherwise file reservations.
        requested += [
            (value, "tree" if (self.root / value).is_dir() else "file")
            for value in work_areas
        ]
        for value, scope in requested:
            if any(char in value for char in "*?[]"):
                fail(
                    "invalid_input",
                    f"Reservations use literal paths, not globs: {value}",
                )
            path = Path(value)
            if os.name == "nt" and any(
                ":" in part or part.endswith((".", " ")) or is_reserved(part)
                for part in path.parts
                if part != path.anchor
            ):
                fail(
                    "invalid_input",
                    "Windows device names, streams, and trailing dots/spaces are not reservation paths",
                )
            path = (self.root / path).resolve()
            try:
                path.relative_to(self.root)
            except ValueError:
                fail("invalid_input", f"Reservation escapes workspace: {value}")
            if (scope == "file" and path.is_dir()) or (
                scope == "tree" and path.is_file()
            ):
                fail("invalid_input", f"Wrong reservation scope: {value}")
            result.append((path.as_posix().casefold(), scope))
        for value in self.args.resource:
            if not re.fullmatch(r"[a-zA-Z0-9_.-]+", value):
                fail(
                    "invalid_input",
                    "Resource names use letters, numbers, underscore, dot, or hyphen",
                )
            result.append(("resource:" + value.casefold(), "resource"))
        return sorted(set(result))

    def conflicts(self, task, keys):
        for row in self.db.execute(
            "SELECT * FROM reservations WHERE task_id!=?", (task,)
        ):
            for path, scope in keys:
                other = row["path"]
                if (
                    path == other
                    or (scope == "tree" and other.startswith(path.rstrip("/") + "/"))
                    or (
                        row["scope"] == "tree"
                        and path.startswith(other.rstrip("/") + "/")
                    )
                ):
                    return f"{path} conflicts with {row['task_id']}: {other} (including expired reservations)"
        return None

    def claim(self):
        args = self.args
        candidates = (
            sorted(
                self.task_rows("todo"),
                key=task_order,
            )
            if (args.command == "claim-next")
            else [self.row(args.task)]
        )
        for row in candidates:
            allowed = ("blocked", "review") if args.command == "resume" else ("todo",)
            if row["status"] not in allowed or row["token"] or not self.ready(row):
                if args.command == "claim-next":
                    continue
                fail(
                    "conflict",
                    "Task not claimable: check status, dependencies, and abandoned lease",
                )
            if args.command == "resume" and args.revision != row["revision"]:
                fail(
                    "conflict",
                    f"Revision changed; current revision is {row['revision']}",
                )
            try:
                keys = self.reservation_keys(
                    json.loads(row["spec"])["work_areas"]
                    if args.reserve_work_areas
                    else ()
                )
            except DibsError as error:
                fail(error.code, f"{row['id']}: {error}")
            conflict = self.conflicts(row["id"], keys)
            if conflict:
                if args.command == "claim-next":
                    continue
                fail("conflict", conflict)
            token, expires = uuid.uuid4().hex, time.time() + args.lease_seconds
            self.db.execute(
                """UPDATE tasks SET status='in_progress',owner=?,token=?,expires=?,
                revision=revision+1,updated=? WHERE id=?""",
                (args.actor, token, expires, time.time(), row["id"]),
            )
            for path, scope in keys:
                self.db.execute(
                    "INSERT INTO reservations VALUES (?,?,?,?,?)",
                    (path, scope, row["id"], token, expires),
                )
            self.event(args.command, row["id"], {"reservations": keys})
            return {"task": self.detail(row["id"]), "lease_token": token}
        fail("conflict", "No ready task with the requested reservations")

    def check_revision(self, row):
        if self.args.revision is not None and self.args.revision != row["revision"]:
            fail("conflict", f"Revision changed; current revision is {row['revision']}")

    def require_lease(self, row, allow_expired=False):
        # Only the lease holder can change owned work, so the revision is an optional extra guard.
        self.check_revision(row)
        if (
            not row["token"]
            or row["token"] != self.args.token
            or row["owner"] != self.args.actor
            or (row["expires"] <= time.time() and not allow_expired)
        ):
            fail("conflict", "A valid, unexpired owner lease is required")

    def handoff(self, task):
        data = read_json(self.args.file)
        fields = {"summary", "next_steps", "changed_files", "checks", "blockers"}
        if not isinstance(data, dict) or "summary" not in data or set(data) - fields:
            fail(
                "invalid_input",
                "Handoff requires summary; optional arrays: "
                + ", ".join(sorted(fields - {"summary"})),
            )
        data = {key: [] for key in fields} | data
        nonempty(data["summary"], "summary")
        for key in fields - {"summary"}:
            if not isinstance(data[key], list):
                fail("invalid_input", f"{key} must be an array of text")
            for value in data[key]:
                nonempty(value, key)
        if self.args.command in ("complete", "review") and not data["checks"]:
            fail("invalid_input", "Completion/review requires check evidence")
        if self.args.command == "complete" and data["blockers"]:
            fail("invalid_input", "Cannot complete with unresolved blockers")
        if self.args.command == "block" and not data["blockers"]:
            fail("invalid_input", "Blocking requires a specific blocker")
        self.db.execute(
            "INSERT INTO handoffs(task_id,actor,data,timestamp) VALUES (?,?,?,?)",
            (task, self.args.actor, encoded(data), time.time()),
        )
        return data

    def end_ownership(self, row):
        status = {
            "block": "blocked",
            "review": "review",
            "complete": "done",
            "cancel": "cancelled",
        }[self.args.command]
        if status == "done" and not self.ready(row):
            fail("conflict", "Unfinished dependencies prevent completion")
        self.db.execute(
            "UPDATE tasks SET status=?,owner=NULL,token=NULL,expires=NULL WHERE id=?",
            (status, row["id"]),
        )
        self.db.execute("DELETE FROM reservations WHERE task_id=?", (row["id"],))

    def mutate(self):
        args = self.args
        row = self.row(args.task)
        if args.command == "reclaim":
            if not args.ack_quiescent:
                fail(
                    "invalid_input",
                    "Reclaim requires --ack-quiescent after stopping/inspecting the old worker",
                )
            if not row["token"] or row["expires"] > time.time():
                fail("conflict", "Only abandoned leases can be reclaimed")
            # Expiry materialization may bump revision by one; require the fresh show revision.
            if args.revision != row["revision"]:
                fail(
                    "conflict",
                    f"Revision changed; current revision is {row['revision']}",
                )
            token, expires = uuid.uuid4().hex, time.time() + args.lease_seconds
            self.db.execute(
                "UPDATE tasks SET status='in_progress',owner=?,token=?,expires=? WHERE id=?",
                (args.actor, token, expires, args.task),
            )
            self.db.execute(
                "UPDATE reservations SET token=?,expires=? WHERE task_id=?",
                (token, expires, args.task),
            )
            data = {"previous_owner": row["owner"], "quiescent_acknowledged": True}
        elif args.command == "note":
            # Notes only append to the audit log, so any actor may add one.
            self.check_revision(row)
            nonempty(args.message, "message")
            data = {"message": args.message}
        elif args.command in ("block", "cancel") and not row["token"]:
            # Unclaimed work can be deferred or dropped; the transaction lock guarantees nobody holds a lease.
            if args.revision != row["revision"] or not args.ack_unowned:
                fail(
                    "conflict",
                    "Unclaimed block/cancel requires current --revision and --ack-unowned",
                )
            if (
                row["status"]
                not in {
                    "block": ("todo", "review"),
                    "cancel": ("todo", "blocked", "review"),
                }[args.command]
            ):
                fail("conflict", f"Cannot {args.command} a {row['status']} task")
            data = self.handoff(args.task)
            self.end_ownership(row)
        else:
            # A matching token proves nobody reclaimed, so the late owner may renew an expired lease.
            self.require_lease(row, allow_expired=args.command == "heartbeat")
            data = {}
            if args.command == "heartbeat":
                if row["expires"] <= time.time():
                    data = {"recovered": True}
                expires = time.time() + args.lease_seconds
                self.db.execute(
                    "UPDATE tasks SET status='in_progress',expires=? WHERE id=?",
                    (expires, args.task),
                )
                self.db.execute(
                    "UPDATE reservations SET expires=? WHERE task_id=?",
                    (expires, args.task),
                )
            elif args.command in ("reserve", "release"):
                keys = self.reservation_keys()
                if not keys:
                    fail("invalid_input", "Specify at least one reservation")
                conflict = self.conflicts(args.task, keys)
                if args.command == "reserve" and conflict:
                    fail("conflict", conflict)
                for path, scope in keys:
                    if args.command == "reserve":
                        self.db.execute(
                            "INSERT OR IGNORE INTO reservations VALUES (?,?,?,?,?)",
                            (path, scope, args.task, row["token"], row["expires"]),
                        )
                    else:
                        deleted = self.db.execute(
                            "DELETE FROM reservations WHERE path=? AND scope=? AND task_id=?",
                            (path, scope, args.task),
                        ).rowcount
                        if not deleted:
                            fail("conflict", f"Reservation not owned: {path}")
                data = {"reservations": keys}
            else:
                data = self.handoff(args.task)
                if args.command in ("block", "review", "complete", "cancel"):
                    if not args.ack_quiescent:
                        fail(
                            "invalid_input",
                            "Releasing ownership requires --ack-quiescent (editing stopped)",
                        )
                    self.end_ownership(row)
        self.db.execute(
            "UPDATE tasks SET revision=revision+1,updated=? WHERE id=?",
            (time.time(), args.task),
        )
        self.event(args.command, args.task, data)
        result = {"task": self.detail(args.task)}
        if args.command == "reclaim":
            result["lease_token"] = token
        return result

    def amend(self):
        args = self.args
        row = self.row(args.task)
        if row["token"]:
            self.require_lease(row)
        elif args.revision != row["revision"] or not args.ack_unowned:
            fail(
                "conflict",
                "Unowned amendments require current --revision and --ack-unowned",
            )
        spec = read_json(args.file)
        validate_spec(spec)
        if spec["id"] != args.task:
            fail("invalid_input", "Cannot rename task IDs")
        specs = self.specs()
        specs[args.task] = spec
        validate_graph(specs)
        if row["status"] in ("in_progress", "review", "done") and any(
            self.row(dep)["status"] != "done" for dep in spec["depends_on"]
        ):
            fail(
                "conflict",
                "Cannot add unfinished prerequisites to active/completed work",
            )
        self.db.execute(
            """UPDATE tasks SET spec=?,priority=?,task_type=?,revision=revision+1,updated=?
            WHERE id=?""",
            (
                encoded(spec),
                spec["priority"],
                spec.get("type", "task"),
                time.time(),
                args.task,
            ),
        )
        self.replace_tags(args.task, spec.get("tags", []))
        self.db.execute("DELETE FROM dependencies WHERE task_id=?", (args.task,))
        for dep in spec["depends_on"]:
            self.db.execute("INSERT INTO dependencies VALUES (?,?)", (args.task, dep))
        self.event(
            "amend", args.task, {"before": json.loads(row["spec"]), "after": spec}
        )
        return {"task": self.detail(args.task)}

    def tag(self):
        args = self.args
        self.row(args.task)
        if not args.add_tag and not args.remove_tag:
            fail("invalid_input", "Specify --add or --remove")
        overlap = set(args.add_tag) & set(args.remove_tag)
        if overlap:
            fail("invalid_input", "Cannot add and remove the same tag")
        before = [
            row[0]
            for row in self.db.execute(
                "SELECT tag FROM task_tags WHERE task_id=? ORDER BY tag", (args.task,)
            )
        ]
        tags = sorted((set(before) | set(args.add_tag)) - set(args.remove_tag))
        if tags == sorted(before):
            fail("conflict", "Tags would not change")
        self.db.execute(
            "UPDATE tasks SET updated=? WHERE id=?",
            (time.time(), args.task),
        )
        self.replace_tags(args.task, tags)
        self.event("tag", args.task, {"before": before, "after": tags})
        return {"task": self.detail(args.task)}

    def read(self):
        args = self.args
        if args.command == "show":
            return {"task": self.detail(args.task)}
        if args.command == "events":
            if args.task:
                self.row(args.task)
            rows = self.db.execute(
                """SELECT * FROM events WHERE sequence>? AND (? IS NULL OR task_id=?)
                ORDER BY sequence LIMIT ?""",
                (args.after, args.task, args.task, args.limit),
            )
            return {"events": [dict(r) | {"data": json.loads(r["data"])} for r in rows]}
        tasks = []
        for row in sorted(self.task_rows(), key=task_order):
            detail = self.detail(row["id"])
            detail["ready"] = (
                row["status"] == "todo" and not row["token"] and self.ready(row)
            )
            if args.command == "next" and not detail["ready"]:
                continue
            tasks.append(detail)
        if args.command == "export":
            return {
                "schema_version": VERSION,
                "tasks": tasks,
                "events": [
                    dict(r) | {"data": json.loads(r["data"])}
                    for r in self.db.execute("SELECT * FROM events ORDER BY sequence")
                ],
                "metadata": dict(self.db.execute("SELECT key,value FROM metadata")),
            }
        return {"tasks": tasks}

    def run(self):
        args = self.args
        if args.command == "init":
            return self.initialize()
        if args.command == "backup":
            target = Path(args.file).resolve()
            if target == self.path:
                fail(
                    "invalid_input",
                    "Backup destination must differ from the live database",
                )
            # Exclusive creation prevents accidental replacement of an earlier backup.
            with target.open("xb"):
                pass
            try:
                deadline = time.monotonic() + 10

                def progress(status, remaining, total):
                    if time.monotonic() > deadline:
                        fail("storage", "Backup timed out; retry after writers finish")

                with closing(sqlite3.connect(target)) as dest:
                    self.db.backup(dest, pages=128, progress=progress, sleep=0.05)
                return {"backup": str(target)}
            except BaseException:
                target.unlink(missing_ok=True)
                raise
        if args.command in ("list", "show", "next", "events", "export"):
            self.db.execute("BEGIN")
            result = self.read()
            self.db.commit()
            if args.command == "export" and args.file:
                target = Path(args.file)
                # Exclusive exports avoid overwriting another agent's snapshot.
                with target.open("x", encoding="utf-8") as out:
                    json.dump(result, out, indent=2, ensure_ascii=False)
                    out.write("\n")
                return {
                    "export": str(target.resolve()),
                    "task_count": len(result["tasks"]),
                }
            return result
        self.begin()
        self.expire()
        # Materialize expiry even when the following stale mutation is rejected.
        self.db.commit()
        self.begin()
        if args.command == "import":
            result = self.import_tasks()
        elif args.command in ("claim", "claim-next", "resume"):
            result = self.claim()
        elif args.command == "amend":
            result = self.amend()
        elif args.command == "tag":
            result = self.tag()
        else:
            result = self.mutate()
        self.db.commit()
        return result


class Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault(
            "formatter_class",
            lambda prog: argparse.RawDescriptionHelpFormatter(
                prog, width=96, max_help_position=30
            ),
        )
        super().__init__(*args, **kwargs)

    def error(self, message):
        fail("invalid_input", message)


def parser():
    common = Parser(add_help=False)
    general = common.add_argument_group("common options")
    env = os.environ.get
    general.add_argument(
        "--workspace",
        metavar="DIR",
        default=env("DIBS_WORKSPACE"),
        required=not env("DIBS_WORKSPACE"),
        help="Workspace directory (required unless DIBS_WORKSPACE is set)",
    )
    general.add_argument(
        "--db",
        metavar="PATH",
        default=env("DIBS_DB"),
        help="Database (default: DIBS_DB, else WORKSPACE/.dibs/tasks.sqlite3)",
    )
    general.add_argument(
        "--actor",
        metavar="NAME",
        default=env("DIBS_ACTOR") or env("USERNAME", "local"),
        help="Agent identity (default: DIBS_ACTOR, USERNAME, or local)",
    )
    general.add_argument(
        "--json", action="store_true", help="Compact JSON output, including errors"
    )
    general.add_argument(
        "--busy-timeout",
        metavar="MS",
        type=int,
        default=2000,
        help="Wait per lock attempt (default: 2000 ms; up to 3 attempts)",
    )
    p = Parser(
        usage="%(prog)s COMMAND [options]",
        description="Coordinate local agent tasks, ownership, and file reservations.",
        epilog="""Examples:
  %(prog)s next --workspace . --json
  %(prog)s show COMPAT-001 --workspace .
  %(prog)s claim COMPAT-001 --workspace . --actor agent-1 --reserve-tree tests/fixtures

Put options after the command. Run COMMAND --help for its arguments.
Environment defaults: DIBS_WORKSPACE, DIBS_DB, DIBS_ACTOR, DIBS_TOKEN.
Exit codes: 0 success | 1 internal | 2 conflict | 3 invalid input | 4 storage | 5 not found""",
    )
    sub = p.add_subparsers(
        dest="command", required=True, title="commands", metavar="COMMAND", prog=p.prog
    )
    commands = {
        "init": "Initialize or migrate the coordination database",
        "import": "Import task specifications without resetting progress",
        "list": "List tasks and their current status",
        "show": "Show a task's specification, lease, and handoffs",
        "next": "List tasks whose dependencies are complete",
        "claim": "Claim a task and reserve its requested paths",
        "claim-next": "Claim the next ready task with compatible reservations",
        "heartbeat": "Renew an owned task's lease and reservations",
        "reserve": "Reserve additional files, subtrees, or named resources",
        "release": "Release selected reservations while keeping the task",
        "note": "Append a finding or progress note (no lease needed)",
        "handoff": "Record a handoff while retaining ownership",
        "block": "Record a blocker and release ownership, or defer unclaimed work",
        "resume": "Claim blocked or reviewed work with a fresh lease",
        "review": "Submit evidence for review and release ownership",
        "complete": "Record completion evidence and release ownership",
        "cancel": "Cancel owned or unclaimed work and release its reservations",
        "amend": "Update a task specification using its current revision",
        "tag": "Add or remove task tags without changing task ownership",
        "reclaim": "Take over an expired lease after the old worker stops",
        "export": "Export a readable status and audit snapshot",
        "backup": "Create a consistent SQLite database backup",
        "events": "Read the append-only audit log",
    }
    for name, description in commands.items():
        has_task = name not in (
            "init",
            "import",
            "list",
            "next",
            "claim-next",
            "export",
            "backup",
            "events",
        )
        s = sub.add_parser(
            name,
            parents=[common],
            help=description,
            description=description + ".",
            usage="%(prog)s" + (" TASK" if has_task else "") + " [options]",
        )
        if has_task:
            s.add_argument("task", metavar="TASK", help="Task ID, e.g. COMPAT-001")
        options = s.add_argument_group("command options")
        if name == "init":
            options.add_argument(
                "--journal",
                choices=("wal", "delete"),
                default="wal",
                help="Journal mode (default: wal; use delete for older SQLite)",
            )
        if name in (
            "import",
            "handoff",
            "block",
            "review",
            "complete",
            "cancel",
            "amend",
            "backup",
            "export",
        ):
            file_help = (
                "New snapshot destination"
                if name in ("backup", "export")
                else "JSON input file, or - for stdin"
            )
            options.add_argument(
                "--file",
                metavar="PATH",
                required=name != "export",
                help=file_help
                + (" (required)" if name != "export" else " (default: stdout)"),
            )
        if name in ("list", "next", "export"):
            options.add_argument(
                "--status",
                metavar="STATE",
                choices=STATUSES,
                help="Filter: " + ", ".join(STATUSES),
            )
        if name in ("list", "next", "claim-next", "export"):
            options.add_argument(
                "--type",
                dest="task_type",
                type=lambda value: label(value, "type"),
                help="Only this task type",
            )
            options.add_argument(
                "--tag",
                action="append",
                default=[],
                type=lambda value: label(value, "tag"),
                help="Require a tag (repeat to require all)",
            )
            for flag, help_text in (
                ("created-after", "Created at or after this ISO-8601 timestamp"),
                ("created-before", "Created at or before this ISO-8601 timestamp"),
                ("updated-after", "Updated at or after this ISO-8601 timestamp"),
                ("updated-before", "Updated at or before this ISO-8601 timestamp"),
            ):
                options.add_argument(
                    "--" + flag,
                    type=parse_timestamp,
                    metavar="TIME",
                    help=help_text,
                )
        if name in ("claim", "claim-next", "resume", "reserve", "release"):
            paths = s.add_argument_group(
                "reservations (repeatable; literal paths, not globs)"
            )
            paths.add_argument(
                "--reserve-file",
                metavar="PATH",
                action="append",
                default=[],
                help="Exact workspace file",
            )
            paths.add_argument(
                "--reserve-tree",
                metavar="DIR",
                action="append",
                default=[],
                help="Workspace directory and everything beneath it",
            )
            paths.add_argument(
                "--resource",
                metavar="NAME",
                action="append",
                default=[],
                help="Shared resource, e.g. git-index",
            )
            if name in ("claim", "claim-next", "resume"):
                paths.add_argument(
                    "--reserve-work-areas",
                    action="store_true",
                    help="Also reserve the claimed task's work_areas (dirs as trees)",
                )
        ownership = s.add_argument_group("ownership and lease")
        if name in ("claim", "claim-next", "resume", "heartbeat", "reclaim"):
            ownership.add_argument(
                "--lease-seconds",
                metavar="SECONDS",
                type=int,
                default=600,
                help="Lease duration (default: 600; range: 1..86400)",
            )
        if name in (
            "heartbeat",
            "reserve",
            "release",
            "note",
            "handoff",
            "block",
            "review",
            "complete",
            "cancel",
            "amend",
            "resume",
            "reclaim",
        ):
            required = name in ("resume", "reclaim")
            ownership.add_argument(
                "--revision",
                metavar="N",
                type=int,
                required=required,
                help="Latest returned task revision"
                + (
                    " (required)"
                    if required
                    else " (optional guard with a lease; required for unclaimed amend/block/cancel)"
                ),
            )
        if name in (
            "heartbeat",
            "reserve",
            "release",
            "handoff",
            "block",
            "review",
            "complete",
            "cancel",
            "amend",
        ):
            unclaimed_ok = name in ("amend", "block", "cancel")
            ownership.add_argument(
                "--token",
                metavar="TOKEN",
                default=env("DIBS_TOKEN"),
                required=not unclaimed_ok and not env("DIBS_TOKEN"),
                help="Lease token from claim/resume/reclaim (default: DIBS_TOKEN)"
                + (" (required for owned tasks)" if unclaimed_ok else ""),
            )
        if name in ("block", "review", "complete", "cancel", "reclaim"):
            ownership.add_argument(
                "--ack-quiescent",
                action="store_true",
                help="Acknowledge editing stopped before release/takeover",
            )
        if name in ("amend", "block", "cancel"):
            ownership.add_argument(
                "--ack-unowned",
                action="store_true",
                help="Acknowledge acting on unclaimed work (requires --revision)",
            )
        if name == "tag":
            options.add_argument(
                "--add",
                dest="add_tag",
                action="append",
                default=[],
                type=lambda value: label(value, "tag"),
                metavar="TAG",
                help="Add a tag (repeatable)",
            )
            options.add_argument(
                "--remove",
                dest="remove_tag",
                action="append",
                default=[],
                type=lambda value: label(value, "tag"),
                metavar="TAG",
                help="Remove a tag (repeatable)",
            )
        if name == "note":
            options.add_argument(
                "--message",
                metavar="TEXT",
                required=True,
                help="Finding or progress note (required)",
            )
        if name == "events":
            options.add_argument(
                "--task", metavar="TASK", help="Only events for this task"
            )
            options.add_argument(
                "--after",
                metavar="N",
                type=int,
                default=0,
                help="Only sequences greater than N (default: 0)",
            )
            options.add_argument(
                "--limit",
                metavar="N",
                type=int,
                default=100,
                help="Maximum events (default: 100; range: 1..10000)",
            )
    return p


def human_output(result, command):
    """Small text views for inspection; --json retains the complete API response."""

    def one_line(value):
        return " ".join(str(value).split())

    def timestamp(value):
        return datetime.fromtimestamp(value, timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

    if not result["ok"]:
        error = result["error"]
        return f"Error ({error['code']}): {error['message']}"
    if command == "export" and "tasks" in result:
        return json.dumps(result, ensure_ascii=False, indent=2)
    if "tasks" in result:
        tasks = result["tasks"]
        if not tasks:
            return "No ready tasks." if command == "next" else "No matching tasks."
        rows = [
            [
                t["id"],
                t["priority"],
                t["status"],
                "yes" if t.get("ready") else "-",
                one_line(t["owner"] or "-"),
                timestamp(t["updated"])[:10],
                one_line(t["spec"]["title"]),
            ]
            for t in tasks
        ]
        headers = ["TASK", "PRI", "STATUS", "READY", "OWNER", "UPDATED", "TITLE"]
        widths = [
            max(len(row[i]) for row in [headers, *rows])
            for i in range(len(headers) - 1)
        ]

        def line(row):
            return (
                "  ".join(value.ljust(width) for value, width in zip(row[:-1], widths))
                + "  "
                + row[-1]
            )

        return "\n".join(
            [
                line(headers),
                *map(line, rows),
                "",
                f"{len(tasks)} task(s). Use show TASK for details.",
            ]
        )
    if "task" in result:
        task = result["task"]
        spec = task["spec"]
        lines = [
            f"{task['id']}  {spec['title']}",
            f"Status: {task['status']} | Priority: {task['priority']} | Revision: {task['revision']}",
            f"Type: {spec['type']} | Tags: {', '.join(spec['tags']) or 'none'}",
            f"Created: {timestamp(task['created'])} | Updated: {timestamp(task['updated'])}",
            f"Owner: {task['owner'] or '-'}",
        ]
        if task["expires"] is not None:
            lines.append(
                f"Lease expires: {timestamp(task['expires'])}"
                + (" (abandoned)" if task["abandoned"] else "")
            )
        if "lease_token" in result:
            lines.append(f"Lease token: {result['lease_token']}")
        if task["reservations"]:
            lines.append("Reservations:")
            lines.extend(f"  - {r['scope']}: {r['path']}" for r in task["reservations"])
        if command == "show":
            lines.extend(
                [
                    f"Depends on: {', '.join(spec['depends_on']) or 'none'}",
                    "",
                    spec["description"],
                ]
            )
            for title, values in (
                ("Work areas", spec["work_areas"]),
                ("Acceptance", spec["acceptance"]),
            ):
                lines.extend(["", title + ":", *[f"  - {value}" for value in values]])
        if task["handoffs"] and command in ("show", "resume", "reclaim"):
            latest = task["handoffs"][-1]
            data = latest["data"]
            lines.extend(
                [
                    "",
                    f"Latest handoff ({latest['actor']}, {timestamp(latest['timestamp'])}):",
                    data["summary"],
                ]
            )
            for key, label in (
                ("next_steps", "Next steps"),
                ("checks", "Checks"),
                ("blockers", "Blockers"),
                ("changed_files", "Changed files"),
            ):
                if data[key]:
                    lines.extend(
                        [label + ":", *[f"  - {value}" for value in data[key]]]
                    )
            if len(task["handoffs"]) > 1:
                lines.append(
                    f"{len(task['handoffs'])} handoffs total; use show {task['id']} --json for all."
                )
        return "\n".join(lines)
    if "events" in result:
        lines = []
        for event in result["events"]:
            data = event["data"]
            summary = data.get("message") or data.get("summary") or ""
            lines.append(
                f"{event['sequence']}  {timestamp(event['timestamp'])}  {event['task_id'] or '-'}  "
                f"{event['type']}  {one_line(event['actor'])}"
                + (f"  {one_line(summary)}" if summary else "")
            )
        return "\n".join(lines) if lines else "No matching events."
    if "added" in result:
        return f"Imported {len(result['added'])} task(s); {len(result['unchanged'])} unchanged."
    return "\n".join(
        f"{key.replace('_', ' ').capitalize()}: {value}"
        for key, value in result.items()
        if key not in ("ok", "error")
    )


def main():
    store = None
    args = None
    try:
        args = parser().parse_args()
        nonempty(args.actor, "actor")
        if not 1 <= args.busy_timeout <= 10000:
            fail("invalid_input", "busy-timeout must be 1..10000 ms")
        if hasattr(args, "lease_seconds") and not 1 <= args.lease_seconds <= 86400:
            fail("invalid_input", "lease-seconds must be 1..86400")
        if args.command == "events" and (
            args.after < 0 or not 1 <= args.limit <= 10000
        ):
            fail(
                "invalid_input",
                "events requires nonnegative --after and --limit 1..10000",
            )
        for prefix in ("created", "updated"):
            after = getattr(args, prefix + "_after", None)
            before = getattr(args, prefix + "_before", None)
            if after is not None and before is not None and after > before:
                fail("invalid_input", f"{prefix}-after must not exceed {prefix}-before")
        store = Store(args)
        result = {"ok": True, "error": None, **store.run()}
        code = 0
    except Exception as error:  # noqa: BLE001  # CLI boundary returns structured errors.
        message = str(error)
        if isinstance(error, DibsError):
            kind = error.code
        elif isinstance(error, ValueError):
            kind = "invalid_input"
        elif isinstance(error, FileNotFoundError):
            kind = "not_found"
        elif isinstance(error, FileExistsError):
            kind = "conflict"
        elif isinstance(error, (OSError, sqlite3.Error)):
            kind = "storage"
        else:
            kind, message = "internal", f"{type(error).__name__}: {error}"
        result = {"ok": False, "error": {"code": kind, "message": message}}
        code = EXIT[kind]
    finally:
        if store:
            store.db.close()  # Closing rolls back any failed transaction.
    if "--json" in sys.argv:
        print(encoded(result))
    else:
        print(
            human_output(result, args.command if args else None),
            file=sys.stdout if result["ok"] else sys.stderr,
        )
    return code


if __name__ == "__main__":
    sys.exit(main())

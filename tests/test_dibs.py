import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts/dibs.py"
ROOT = Path(__file__).parents[1]


class TaskMetadataTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_dibs(self, *args):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                *args,
                "--workspace",
                str(self.workspace),
                "--actor",
                "test",
                "--json",
            ],
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, payload)
        return payload

    def run_text(self, *args):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                *args,
                "--workspace",
                str(self.workspace),
                "--actor",
                "test",
            ],
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_init_picks_journal_for_runtime(self):
        sys.path.insert(0, str(SCRIPT.parent))
        from dibs import wal_safe

        expected = "wal" if wal_safe(sqlite3.sqlite_version_info) else "delete"
        self.assertEqual(self.run_dibs("init")["journal"], expected)

    def test_non_ascii_text_survives_any_output_encoding(self):
        self.run_dibs("init")
        plan = {
            "tasks": [
                {
                    "id": "TASK-001",
                    "title": "Ship ✓ → 🚀",
                    "priority": "P1",
                    "depends_on": [],
                    "work_areas": [],
                    "description": "Unicode.",
                    "acceptance": ["ok"],
                }
            ]
        }
        (self.workspace / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        self.run_dibs("import", "--file", str(self.workspace / "plan.json"))
        self.assertIn("Ship ✓ → 🚀", self.run_text("report"))
        task = self.run_dibs("show", "TASK-001")["task"]
        self.assertEqual(task["spec"]["title"], "Ship ✓ → 🚀")

    def test_timestamps_tags_types_and_filters(self):
        self.run_dibs("init")
        plan = self.workspace / "plan.json"
        plan.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "TASK-001",
                            "title": "Add metadata",
                            "priority": "P1",
                            "depends_on": [],
                            "work_areas": [],
                            "description": "Exercise metadata.",
                            "acceptance": ["Test passes"],
                            "type": "feature",
                            "tags": ["database", "audit"],
                        },
                        {
                            "id": "TASK-002",
                            "title": "Legacy task",
                            "priority": "P2",
                            "depends_on": [],
                            "work_areas": [],
                            "description": "Keep the old input shape valid.",
                            "acceptance": ["Import succeeds"],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.run_dibs("import", "--file", str(plan))
        task = self.run_dibs("show", "TASK-001")["task"]
        self.assertEqual(task["spec"]["type"], "feature")
        self.assertEqual(task["spec"]["tags"], ["audit", "database"])
        self.assertLessEqual(task["created"], task["updated"])
        legacy = self.run_dibs("show", "TASK-002")["task"]
        self.assertEqual(legacy["spec"]["type"], "task")
        self.assertEqual(legacy["spec"]["tags"], [])

        cutoff = datetime.fromtimestamp(task["created"] - 1, timezone.utc).isoformat()
        tasks = self.run_dibs(
            "list", "--type", "feature", "--tag", "database", "--created-after", cutoff
        )["tasks"]
        self.assertEqual([item["id"] for item in tasks], ["TASK-001"])

        tagged = self.run_dibs("tag", "TASK-001", "--add", "sqlite")["task"]
        self.assertEqual(tagged["spec"]["tags"], ["audit", "database", "sqlite"])
        self.assertEqual(tagged["revision"], 0)
        self.assertGreaterEqual(tagged["updated"], task["updated"])

    def test_version_two_database_migrates_in_place(self):
        self.run_dibs("init")
        database = self.workspace / ".dibs/tasks.sqlite3"
        with closing(sqlite3.connect(database)) as db:
            for index in (
                "tasks_created",
                "tasks_updated",
                "tasks_type",
                "task_tags_tag",
            ):
                db.execute(f"DROP INDEX {index}")
            db.execute("DROP TABLE task_tags")
            db.execute("ALTER TABLE tasks DROP COLUMN task_type")
            db.execute("PRAGMA user_version=2")
            db.execute("UPDATE metadata SET value='2' WHERE key='schema_version'")
            db.commit()

        result = self.run_dibs("init")
        self.assertEqual(result["schema_version"], 3)
        with closing(sqlite3.connect(database)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertTrue(
                db.execute(
                    "SELECT 1 FROM pragma_table_info('tasks') WHERE name='task_type'"
                ).fetchone()
            )

    def test_list_selected_fields_include_timestamps_and_work_time(self):
        self.run_dibs("init")
        plan = self.workspace / "plan.json"
        plan.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "TASK-001",
                            "title": "Measure work",
                            "priority": "P1",
                            "depends_on": [],
                            "work_areas": [],
                            "description": "Exercise selected list fields.",
                            "acceptance": ["Output is correct"],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.run_dibs("import", "--file", str(plan))
        self.assertIn("TASK-001", self.run_text("list"))
        self.assertIn(
            "TASK-001", self.run_text("list", "--fields", "id,status,created,updated")
        )
        database = self.workspace / ".dibs/tasks.sqlite3"
        with closing(sqlite3.connect(database)) as db:
            db.executemany(
                "INSERT INTO events(task_id,actor,type,data,timestamp) VALUES (?,?,?,?,?)",
                [
                    ("TASK-001", "test", "claim", "{}", 1000),
                    ("TASK-001", "test", "complete", "{}", 1125),
                ],
            )
            db.commit()

        output = self.run_text(
            "list", "--fields", "id,status,created,updated,work-time,title"
        )
        self.assertEqual(
            output.splitlines()[0].split(),
            ["TASK", "STATUS", "CREATED", "UPDATED", "WORK", "TIME", "TITLE"],
        )
        self.assertIn("00:02:05", output)
        self.assertIn("Measure work", output)

    def test_report_prints_paste_ready_markdown(self):
        self.run_dibs("init")
        plan = self.workspace / "plan.json"
        plan.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": f"TASK-00{n}",
                            "title": f"Story {n}",
                            "priority": "P1",
                            "depends_on": [],
                            "work_areas": [],
                            "description": "Report this.",
                            "acceptance": ["Criterion met"],
                            "tags": ["ui"],
                        }
                        for n in (1, 2)
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.run_dibs("import", "--file", str(plan))
        token = self.run_dibs("claim", "TASK-001")["lease_token"]
        self.run_dibs("note", "TASK-001", "--message", "Found the root cause")
        handoff = self.workspace / "handoff.json"
        handoff.write_text(
            json.dumps(
                {
                    "summary": "Shipped the fix",
                    "changed_files": ["src/app.py"],
                    "checks": ["unit tests pass"],
                }
            ),
            encoding="utf-8",
        )
        self.run_dibs(
            "complete", "TASK-001", "--token", token, "--ack-quiescent",
            "--file", str(handoff),
        )

        output = self.run_text("report", "TASK-001")
        for expected in (
            "## TASK-001: Story 1",
            "**Status:** done",
            "- [x] Criterion met",
            "- `src/app.py`",
            "- unit tests pass",
            "note: Found the root cause",
            "complete: Shipped the fix",
        ):
            self.assertIn(expected, output)
        self.assertNotIn("TASK-002", output)

        report = self.run_dibs("report", "--status", "todo", "--tag", "ui")
        self.assertEqual([task["id"] for task in report["tasks"]], ["TASK-002"])
        self.assertIn("- [ ] Criterion met", report["markdown"])


class DistributionPathTest(unittest.TestCase):
    def test_plugin_instructions_do_not_use_workspace_relative_script_paths(self):
        skill = (ROOT / "skills/dibs/SKILL.md").read_text(encoding="utf-8")
        self.assertIn('python "<DIBS_SCRIPT>"', skill)
        self.assertNotIn('python ".github/skills/dibs/scripts/dibs.py"', skill)

        command_dir = ROOT / ".claude/commands"
        expected = "${CLAUDE_PLUGIN_ROOT}/scripts/dibs.py"
        for command in ("events.md", "list.md", "next.md", "report.md", "show.md"):
            content = (command_dir / command).read_text(encoding="utf-8")
            self.assertIn(expected, content)

        claude_plugin = json.loads(
            (ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(claude_plugin["skills"], ["./skills/dibs"])

    def test_codex_read_only_commands_are_explicit_skills(self):
        for command in ("events", "list", "next", "report", "show"):
            skill_dir = ROOT / "skills" / command
            skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            metadata = (skill_dir / "agents/openai.yaml").read_text(encoding="utf-8")
            self.assertIn(f"name: {command}", skill)
            self.assertIn("../../scripts/dibs.py", skill)
            self.assertIn("allow_implicit_invocation: false", metadata)

    def test_plugin_versions_match_marketplace_version(self):
        paths = (
            ROOT / ".claude-plugin/plugin.json",
            ROOT / ".claude-plugin/marketplace.json",
            ROOT / ".codex-plugin/plugin.json",
        )
        claude_plugin, marketplace, codex_plugin = (
            json.loads(path.read_text(encoding="utf-8")) for path in paths
        )
        self.assertEqual(claude_plugin["version"], marketplace["plugins"][0]["version"])
        self.assertEqual(claude_plugin["version"], codex_plugin["version"])


if __name__ == "__main__":
    unittest.main()

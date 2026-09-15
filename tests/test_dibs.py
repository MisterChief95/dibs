import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "skills/dibs/scripts/dibs.py"
ROOT = Path(__file__).parents[1]


class TaskMetadataTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_dibs(self, *args):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *args, "--workspace", str(self.workspace), "--actor", "test", "--json"],
            text=True,
            capture_output=True,
            check=False,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, payload)
        return payload

    def test_timestamps_tags_types_and_filters(self):
        self.run_dibs("init", "--journal", "delete")
        plan = self.workspace / "plan.json"
        plan.write_text(
            json.dumps({"tasks": [
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
            ]}),
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

        tagged = self.run_dibs(
            "tag", "TASK-001", "--add", "sqlite"
        )["task"]
        self.assertEqual(tagged["spec"]["tags"], ["audit", "database", "sqlite"])
        self.assertEqual(tagged["revision"], 0)
        self.assertGreaterEqual(tagged["updated"], task["updated"])

    def test_version_two_database_migrates_in_place(self):
        self.run_dibs("init", "--journal", "delete")
        database = self.workspace / ".dibs/tasks.sqlite3"
        with closing(sqlite3.connect(database)) as db:
            for index in ("tasks_created", "tasks_updated", "tasks_type", "task_tags_tag"):
                db.execute(f"DROP INDEX {index}")
            db.execute("DROP TABLE task_tags")
            db.execute("ALTER TABLE tasks DROP COLUMN task_type")
            db.execute("PRAGMA user_version=2")
            db.execute("UPDATE metadata SET value='2' WHERE key='schema_version'")
            db.commit()

        result = self.run_dibs("init", "--journal", "delete")
        self.assertEqual(result["schema_version"], 3)
        with closing(sqlite3.connect(database)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertTrue(db.execute(
                "SELECT 1 FROM pragma_table_info('tasks') WHERE name='task_type'"
            ).fetchone())


class DistributionPathTest(unittest.TestCase):
    def test_plugin_instructions_do_not_use_workspace_relative_script_paths(self):
        skill = (ROOT / "skills/dibs/SKILL.md").read_text(encoding="utf-8")
        self.assertIn('python "<DIBS_SCRIPT>"', skill)
        self.assertNotIn('python ".github/skills/dibs/scripts/dibs.py"', skill)

        command_dir = ROOT / ".claude/commands"
        expected = '${CLAUDE_PLUGIN_ROOT}/skills/dibs/scripts/dibs.py'
        for command in ("events.md", "list.md", "next.md", "show.md"):
            content = (command_dir / command).read_text(encoding="utf-8")
            self.assertIn(expected, content)


if __name__ == "__main__":
    unittest.main()

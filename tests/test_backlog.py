import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bl"


def load_bl():
    loader = importlib.machinery.SourceFileLoader("backlogger_bl", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


bl = load_bl()


class BacklogTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_cwd = os.getcwd()
        self.provider_env = {
            "OPENAI_API_KEY": os.environ.pop("OPENAI_API_KEY", None),
            "ANTHROPIC_API_KEY": os.environ.pop("ANTHROPIC_API_KEY", None),
        }
        os.chdir(self.temp.name)
        bl.BACKLOG = "backlog.md"
        self.env = os.environ.copy()

    def tearDown(self):
        os.chdir(self.previous_cwd)
        for key, value in self.provider_env.items():
            if value is not None:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)
        self.temp.cleanup()

    def write_backlog(self, text):
        Path("backlog.md").write_text(text, encoding="utf-8")

    def run_bl(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, args)],
            cwd=self.temp.name,
            env=self.env,
            text=True,
            capture_output=True,
        )


class ParsingAndMigrationTests(BacklogTestCase):
    def test_parse_only_valid_final_id_and_preserve_hashtags(self):
        task = bl.parse_task("- [ ] discuss #api shape #refreshDesign", 4)
        self.assertEqual(task.description, "discuss #api shape")
        self.assertEqual(task.task_id, "refreshDesign")
        self.assertEqual(task.line_number, 4)
        self.assertIsNone(bl.parse_task("- [ ] malformed #12").task_id)
        self.assertIsNone(bl.parse_task("- [ ] short #abcd").task_id)

    def test_first_read_migrates_active_and_archive_once(self):
        original = "heading\n- [ ] active #api note\n---\n- [x] archived\n"
        self.write_backlog(original)
        first = bl.list_tasks()
        migrated = Path("backlog.md").read_bytes()
        second = bl.list_tasks()
        self.assertEqual(migrated, Path("backlog.md").read_bytes())
        self.assertEqual(first, second)
        self.assertRegex(first["active"][0]["id"], r"^[A-Za-z][A-Za-z0-9]{4,5}$")
        self.assertRegex(first["archive"][0]["id"], r"^[A-Za-z][A-Za-z0-9]{4,5}$")
        self.assertIn("heading\n", migrated.decode())
        self.assertIn("active #api note #", migrated.decode())

    def test_duplicate_ids_fail_with_both_locations_without_write(self):
        original = "- [ ] one #sameId\n---\n- [x] two #sameId\n"
        self.write_backlog(original)
        with self.assertRaisesRegex(bl.BacklogError, "active line 1 and archive line 1"):
            bl.list_tasks()
        self.assertEqual(Path("backlog.md").read_text(), original)

    def test_rejected_operation_does_not_commit_migration(self):
        original = "- [>] busy\n- [ ] old\n"
        self.write_backlog(original)
        with self.assertRaisesRegex(bl.BacklogError, "priority"):
            bl.add_task("ASAP FIX urgent #urgent")
        self.assertEqual(Path("backlog.md").read_text(), original)

    def test_missing_show_transaction_does_not_create_backlog(self):
        self.assertEqual(bl.list_tasks(), {"active": [], "archive": []})
        self.assertFalse(Path("backlog.md").exists())

    def test_migration_preserves_separator_spelling_and_crlf(self):
        Path("backlog.md").write_bytes(b"- [ ] active\r\n  --- \r\n- [x] old\r\n")
        bl.list_tasks()
        migrated = Path("backlog.md").read_bytes()
        self.assertIn(b"\r\n  --- \r\n", migrated)
        self.assertNotIn(b"\n", migrated.replace(b"\r\n", b""))


class TransactionAndCommandTests(BacklogTestCase):
    def test_explicit_id_status_flow_and_archive_target_error(self):
        result = bl.add_task("ship feature #shipFeature")
        self.assertEqual(result["id"], "shipFeature")
        bl.set_task_status("shipFeature", bl.STATUS_TAKEN)
        with self.assertRaisesRegex(bl.BacklogError, "not available"):
            bl.set_task_status("shipFeature", bl.STATUS_TAKEN)
        bl.set_task_status("shipFeature", bl.STATUS_DONE)
        self.assertEqual(bl.purge_tasks()["count"], 1)
        with self.assertRaisesRegex(bl.BacklogError, "archived"):
            bl.set_task_status("shipFeature", bl.STATUS_EMPTY)

    def test_numeric_target_is_current_physical_line_and_rejects_non_task(self):
        self.write_backlog("note\n- [ ] task #taskA\n")
        with self.assertRaisesRegex(bl.BacklogError, "not a task"):
            bl.set_task_status("1", bl.STATUS_TAKEN)
        bl.set_task_status("2", bl.STATUS_TAKEN)
        self.assertIn("- [>] task #taskA", Path("backlog.md").read_text())

    def test_edit_detects_text_conflict_but_preserves_status_change(self):
        self.write_backlog("- [ ] original #editMe\n")
        bl.set_task_status("editMe", bl.STATUS_TAKEN)
        result = bl.edit_task("editMe", "original", "new text")
        self.assertEqual(result["status"], bl.STATUS_TAKEN)
        self.assertEqual(Path("backlog.md").read_text(), "- [>] new text #editMe\n")
        with self.assertRaisesRegex(bl.BacklogError, "edited elsewhere"):
            bl.edit_task("editMe", "original", "stale draft")

    def test_explicit_duplicate_rejected_and_malformed_hashtag_gets_id(self):
        bl.add_task("one #sameId")
        before = Path("backlog.md").read_bytes()
        with self.assertRaisesRegex(bl.BacklogError, "already exists"):
            bl.add_task("two #sameId")
        self.assertEqual(Path("backlog.md").read_bytes(), before)
        result = bl.add_task("three #123")
        self.assertRegex(result["id"], r"^[A-Za-z][A-Za-z0-9]{4,5}$")
        self.assertIn(f"three #123 #{result['id']}", Path("backlog.md").read_text())

    def test_atomic_write_preserves_mode_and_persistent_lock(self):
        self.write_backlog("- [ ] task #modeId\n")
        os.chmod("backlog.md", 0o640)
        bl.set_task_status("modeId", bl.STATUS_TAKEN)
        self.assertEqual(stat.S_IMODE(os.stat("backlog.md").st_mode), 0o640)
        self.assertTrue(Path(".backlog.lock").exists())

    def test_new_backlog_mode_respects_umask(self):
        previous = os.umask(0o077)
        try:
            bl.add_task("private #privateId")
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE(os.stat("backlog.md").st_mode), 0o600)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root bypasses mode checks")
    def test_read_only_backlog_rejects_replacement(self):
        self.write_backlog("- [ ] task #readOnly\n")
        os.chmod("backlog.md", 0o444)
        before = Path("backlog.md").read_bytes()
        try:
            with self.assertRaisesRegex(bl.BacklogError, "not writable"):
                bl.set_task_status("readOnly", bl.STATUS_TAKEN)
            self.assertEqual(Path("backlog.md").read_bytes(), before)
        finally:
            os.chmod("backlog.md", 0o644)

    def test_replace_failure_preserves_original_and_releases_lock(self):
        self.write_backlog("- [ ] task #atomicId\n")
        before = Path("backlog.md").read_bytes()
        with mock.patch.object(bl.os, "replace", side_effect=OSError("injected")):
            with self.assertRaisesRegex(bl.BacklogError, "Cannot save"):
                bl.set_task_status("atomicId", bl.STATUS_TAKEN)
        self.assertEqual(Path("backlog.md").read_bytes(), before)
        with bl.backlog_lock(timeout=0.1):
            pass
        self.assertEqual(list(Path(".").glob(".backlog.*.tmp")), [])

    def test_hard_link_fails_closed(self):
        self.write_backlog("- [ ] task #hardId\n")
        os.link("backlog.md", "alias.md")
        before = Path("backlog.md").read_bytes()
        with self.assertRaisesRegex(bl.BacklogError, "hard-linked"):
            bl.set_task_status("hardId", bl.STATUS_TAKEN)
        self.assertEqual(Path("backlog.md").read_bytes(), before)

    def test_invalid_utf8_fails_closed(self):
        Path("backlog.md").write_bytes(b"- [ ] broken \xff\n")
        before = Path("backlog.md").read_bytes()
        with self.assertRaisesRegex(bl.BacklogError, "valid UTF-8"):
            bl.list_tasks()
        self.assertEqual(Path("backlog.md").read_bytes(), before)

    def test_lock_wait_has_bounded_failure(self):
        holder_code = (
            "import fcntl,time; "
            "f=open('.backlog.lock','a+'); "
            "fcntl.flock(f,fcntl.LOCK_EX); "
            "print('ready',flush=True); time.sleep(2)"
        )
        holder = subprocess.Popen(
            [sys.executable, "-c", holder_code],
            cwd=self.temp.name,
            text=True,
            stdout=subprocess.PIPE,
        )
        try:
            self.assertEqual(holder.stdout.readline().strip(), "ready")
            with self.assertRaisesRegex(bl.BacklogError, "Timed out"):
                with bl.backlog_lock(timeout=0.1):
                    pass
        finally:
            holder.terminate()
            holder.wait(timeout=2)
            holder.stdout.close()

    def test_backend_json_routes_through_transaction_layer(self):
        request = subprocess.run(
            [sys.executable, str(SCRIPT), "_backend", "add"],
            input='{"text":"from bridge #bridgeId"}',
            cwd=self.temp.name,
            env=self.env,
            text=True,
            capture_output=True,
        )
        envelope = json.loads(request.stdout)
        self.assertEqual(request.returncode, 0)
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["result"]["id"], "bridgeId")
        self.assertIn("#bridgeId", Path("backlog.md").read_text())

    def test_backend_rejects_stale_expected_status_without_write(self):
        self.write_backlog("- [x] finished #staleId\n")
        before = Path("backlog.md").read_bytes()
        request = subprocess.run(
            [sys.executable, str(SCRIPT), "_backend", "status"],
            input=json.dumps({"id": "staleId", "status": " ", "expectedStatus": ">"}),
            cwd=self.temp.name,
            env=self.env,
            text=True,
            capture_output=True,
        )
        envelope = json.loads(request.stdout)
        self.assertEqual(request.returncode, 1)
        self.assertFalse(envelope["ok"])
        self.assertIn("changed", envelope["error"])
        self.assertEqual(Path("backlog.md").read_bytes(), before)

    def test_cli_missing_available_and_default_errors_are_nonzero_without_traceback(self):
        missing = self.run_bl("available")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("does not exist", missing.stderr)

        self.write_backlog("- [ ] one #dupeId\n- [ ] two #dupeId\n")
        duplicate = self.run_bl()
        self.assertEqual(duplicate.returncode, 1)
        self.assertIn("Duplicate task ID", duplicate.stderr)
        self.assertNotIn("Traceback", duplicate.stderr)

    def test_legacy_numeric_operands_are_parsed_with_int(self):
        self.write_backlog("- [ ] one #firstId\n- [ ] two #secondId\n")
        bl.set_task_status(" +1 ", bl.STATUS_TAKEN)
        self.assertIn("- [>] one #firstId", Path("backlog.md").read_text())
        with self.assertRaisesRegex(bl.BacklogError, "was not found"):
            bl.set_task_status("²", bl.STATUS_TAKEN)

    def test_public_edit_move_and_completed_commands(self):
        self.write_backlog("- [ ] one #firstId\n- [x] two #secondId\n")
        self.assertEqual(self.run_bl("edit", "firstId", "renamed").returncode, 0)
        self.assertEqual(self.run_bl("move", "firstId", "down").returncode, 0)
        completed = self.run_bl("completed")
        self.assertEqual(completed.returncode, 0)
        self.assertIn("#secondId", completed.stdout)
        self.assertIn("renamed #firstId", Path("backlog.md").read_text())

    def test_parallel_adds_lose_no_rows(self):
        def add(index):
            return self.run_bl("add", f"task {index}")

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(add, range(16)))
        self.assertTrue(all(result.returncode == 0 for result in results), results)
        data = bl.list_tasks()["active"]
        self.assertEqual(len(data), 16)
        self.assertEqual(len({task["id"] for task in data}), 16)
        self.assertEqual({task["description"] for task in data}, {f"task {i}" for i in range(16)})

    def test_parallel_take_has_exactly_one_winner(self):
        self.write_backlog("- [ ] contested #claimMe\n")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.run_bl("take", "claimMe"), range(2)))
        self.assertEqual(sorted(result.returncode for result in results), [0, 1])
        self.assertEqual(Path("backlog.md").read_text(), "- [>] contested #claimMe\n")

    def test_parallel_first_access_migrates_ids_once(self):
        self.write_backlog("- [ ] one\n- [ ] two\n---\n- [x] old\n")
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.run_bl(), range(6)))
        self.assertTrue(all(result.returncode == 0 for result in results))
        first = Path("backlog.md").read_bytes()
        tasks = bl.list_tasks()
        self.assertEqual(first, Path("backlog.md").read_bytes())
        ids = [task["id"] for task in tasks["active"] + tasks["archive"]]
        self.assertEqual(len(ids), 3)
        self.assertEqual(len(set(ids)), 3)

    def test_provider_wait_does_not_hold_backlog_lock(self):
        self.write_backlog("- [ ] existing #readyId\n")
        started = threading.Event()
        release = threading.Event()
        status_done = threading.Event()
        errors = []

        def slow_candidate(_description):
            started.set()
            release.wait(timeout=2)
            return "model2"

        def add_slowly():
            try:
                bl.add_task("slow provider task")
            except Exception as exc:  # pragma: no cover - assertion below reports it
                errors.append(exc)

        def change_status():
            try:
                bl.set_task_status("readyId", bl.STATUS_TAKEN)
            except Exception as exc:  # pragma: no cover - assertion below reports it
                errors.append(exc)
            finally:
                status_done.set()

        with mock.patch.object(bl, "_semantic_candidate", side_effect=slow_candidate):
            worker = threading.Thread(target=add_slowly)
            status_worker = threading.Thread(target=change_status)
            worker.start()
            try:
                self.assertTrue(started.wait(timeout=1))
                status_worker.start()
                self.assertTrue(status_done.wait(timeout=0.5), "status change waited on provider")
            finally:
                release.set()
                worker.join(timeout=2)
                if status_worker.ident is not None:
                    status_worker.join(timeout=2)
        self.assertFalse(errors)
        self.assertFalse(worker.is_alive())
        self.assertFalse(status_worker.is_alive())
        self.assertIn("- [>] existing #readyId", Path("backlog.md").read_text())


class ProviderAndDocumentationTests(BacklogTestCase):
    class FakeResponse:
        def __init__(self, endpoint, payload, status=200):
            self.endpoint = endpoint
            self.payload = payload
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self):
            return self.endpoint

        def read(self, _limit):
            return self.payload

    class FakeOpener:
        def __init__(self, response):
            self.response = response
            self.request = None
            self.timeout = None

        def open(self, request, timeout):
            self.request = request
            self.timeout = timeout
            return self.response

    def test_provider_selection_and_collision_falls_back_locally(self):
        self.write_backlog("- [ ] existing #model1\n")
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "secret", "ANTHROPIC_API_KEY": "other"}), mock.patch.object(
            bl, "_semantic_candidate", return_value="model1"
        ):
            self.assertEqual(bl._provider_selection(), "openai")
            result = bl.add_task("new task")
        self.assertNotEqual(result["id"], "model1")

    def test_explicit_id_skips_provider(self):
        with mock.patch.object(bl, "_semantic_candidate") as semantic:
            result = bl.add_task("new task #givenId")
        semantic.assert_not_called()
        self.assertEqual(result["id"], "givenId")

    def test_openai_and_anthropic_response_parsing(self):
        cases = [
            (
                "openai",
                bl.OPENAI_ENDPOINT,
                {"OPENAI_API_KEY": "openai-secret"},
                {
                    "status": "completed",
                    "output": [
                        {"type": "message", "content": [{"type": "output_text", "text": "login1"}]}
                    ],
                },
                "login1",
            ),
            (
                "anthropic",
                bl.ANTHROPIC_ENDPOINT,
                {"ANTHROPIC_API_KEY": "anthropic-secret"},
                {"content": [{"type": "text", "text": "auth_1"}]},
                "auth_1",
            ),
        ]
        for provider, endpoint, environment, response_body, expected in cases:
            with self.subTest(provider=provider):
                response = self.FakeResponse(endpoint, json.dumps(response_body).encode())
                opener = self.FakeOpener(response)
                with mock.patch.dict(os.environ, environment, clear=False), mock.patch.object(
                    bl, "_no_redirect_opener", return_value=opener
                ):
                    candidate = bl._fetch_provider_candidate(provider, "new task")
                self.assertEqual(candidate, expected)
                sent = json.loads(opener.request.data)
                self.assertNotIn("backlog", json.dumps(sent).lower())
                self.assertEqual(opener.timeout, bl.PROVIDER_TIMEOUT)
                if provider == "openai":
                    self.assertFalse(sent["store"])
                    self.assertEqual(sent["reasoning"], {"effort": "none"})

    def test_provider_rejects_malformed_redirected_and_oversized_responses(self):
        valid_endpoint = bl.OPENAI_ENDPOINT
        invalid_responses = [
            self.FakeResponse(valid_endpoint, b"not-json"),
            self.FakeResponse(
                valid_endpoint,
                json.dumps(
                    {
                        "status": "completed",
                        "output": [
                            {"type": "message", "content": [{"type": "output_text", "text": "id plus prose"}]}
                        ],
                    }
                ).encode(),
            ),
            self.FakeResponse(valid_endpoint, b"x" * (bl.MAX_PROVIDER_RESPONSE + 1)),
            self.FakeResponse("https://example.invalid/redirect", b"{}"),
        ]
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-secret"}):
            for response in invalid_responses:
                with self.subTest(size=len(response.payload), url=response.endpoint), mock.patch.object(
                    bl, "_no_redirect_opener", return_value=self.FakeOpener(response)
                ):
                    with self.assertRaises((ValueError, json.JSONDecodeError)):
                        bl._fetch_provider_candidate("openai", "new task")

    def test_helper_timeout_and_secret_bearing_error_fall_back_silently(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-secret"}), mock.patch.object(
            bl.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["helper"], 1),
        ):
            self.assertIsNone(bl._semantic_candidate("task"))

        output = io.StringIO()
        with mock.patch.object(
            bl, "_fetch_provider_candidate", side_effect=ValueError("synthetic-secret in body")
        ) as fetch, mock.patch.object(sys, "stdin", io.StringIO("task")), contextlib.redirect_stdout(output):
            self.assertEqual(bl.run_id_helper("openai"), 1)
        fetch.assert_called_once_with("openai", "task")
        self.assertNotIn("synthetic-secret", output.getvalue())

    def test_setup_updates_old_instruction_and_is_idempotent(self):
        old = "Use `bl` to manage the local backlog stored in `backlog.md`. Run `bl help` for available commands.\n"
        Path("AGENTS.md").write_text(old, encoding="utf-8")
        bl.setup()
        bl.setup()
        text = Path("AGENTS.md").read_text(encoding="utf-8")
        self.assertEqual(text.count(bl.BL_INSTRUCTION), 1)
        self.assertIn("only way", text)
        self.assertIn("successfully claim", text)

    def test_help_warns_about_numeric_staleness_and_privacy(self):
        result = self.run_bl("help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("physical", result.stdout)
        self.assertIn("bounded description", result.stdout)
        self.assertIn("macOS/Linux", result.stdout)
        self.assertIn("bl edit", result.stdout)
        self.assertIn("bl move", result.stdout)

    def test_version_reports_major_format_release(self):
        result = self.run_bl("--version")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "bl 2.0.0")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Version 2.0.0 — major backlog-format change", readme)

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("swift"), "requires macOS Swift")
    def test_gui_available_rejects_missing_backlog(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "gui", "available"],
            cwd=self.temp.name,
            env=self.env,
            text=True,
            capture_output=True,
            timeout=90,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not exist", result.stdout + result.stderr)

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("swift"), "requires macOS Swift")
    def test_swift_bridge_drains_large_archive_response(self):
        active = "- [ ] visible #visibleId\n"
        archive = "".join(
            f"- [x] archived task {index} with enough text to fill a pipe #archive{index}\n"
            for index in range(3000)
        )
        self.write_backlog(active + "---\n" + archive)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "gui", "show"],
            cwd=self.temp.name,
            env=self.env,
            text=True,
            capture_output=True,
            timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("#visibleId", result.stdout)


if __name__ == "__main__":
    unittest.main()

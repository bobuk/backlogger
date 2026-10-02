import contextlib
import io
import json
import os
import stat
import sys
import urllib.error
from pathlib import Path
from unittest import mock

from tests import test_backlog as helpers

bl = helpers.bl
FakeResponse = helpers.ProviderAndDocumentationTests.FakeResponse
FakeOpener = helpers.ProviderAndDocumentationTests.FakeOpener


class DoctorBacklogTests(helpers.BacklogTestCase):
    def doctor(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = bl.doctor(["--offline", *args])
        return code, output.getvalue()

    def test_default_is_read_only_even_for_duplicates_and_missing_ids(self):
        original = "heading\n- [ ] no ID\n- [ ] one #sameId\n---\n- [x] two #sameId\n"
        self.write_backlog(original)
        code, output = self.doctor()
        self.assertEqual(code, 1)
        self.assertEqual(Path("backlog.md").read_text(), original)
        self.assertIn("duplicate ID #sameId", output)
        self.assertIn("missing a valid final task ID", output)
        self.assertIn("Proposed backlog repairs", output)
        self.assertIn("No backlog or agent files changed", output)
        self.assertFalse(Path("AGENTS.md").exists())

    def test_missing_backlog_is_not_created_even_with_fix_yes(self):
        code, output = self.doctor("--fix", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("backlog.md is missing", output)
        self.assertIn("bl setup", output)
        self.assertFalse(Path("backlog.md").exists())
        self.assertFalse(Path("AGENTS.md").exists())

    def test_fix_preserves_text_mode_newlines_and_first_duplicate_id(self):
        original = (
            b"# Notes\r\n- [ ] first #sameId\r\n- [ ] legacy #123\r\n"
            b"  --- \r\n- [X] archived #sameId\r\n- [>] busy #busyId"
        )
        Path("backlog.md").write_bytes(original)
        os.chmod("backlog.md", 0o640)
        code, output = self.doctor("--fix", "--yes")
        self.assertEqual(code, 0, output)
        self.assertIn("applied atomically", output)
        after = Path("backlog.md").read_bytes()
        self.assertIn(b"# Notes\r\n- [ ] first #sameId\r\n", after)
        self.assertIn(b"  --- \r\n- [x] archived #", after)
        self.assertIn(b"- [ ] legacy #123 #", after)
        self.assertNotIn(b"\n", after.replace(b"\r\n", b""))
        self.assertFalse(after.endswith(b"\n"))
        self.assertEqual(stat.S_IMODE(os.stat("backlog.md").st_mode), 0o640)
        with bl.backlog_lock():
            document = bl._read_document()
        tasks = list(bl._task_locations(document))
        self.assertEqual(len({task.task_id for task in tasks}), 4)
        busy = next(task for task in tasks if task.task_id == "busyId")
        self.assertEqual((busy.section, busy.status), ("active", ">"))
        again, _ = self.doctor("--fix", "--yes")
        self.assertEqual(again, 0)
        self.assertEqual(Path("backlog.md").read_bytes(), after)

    def test_diagnosis_does_not_reset_claims_or_purge_completed_tasks(self):
        original = "- [>] busy #busyId\n- [x] done #doneId\n"
        self.write_backlog(original)
        code, output = self.doctor("--fix", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("bl purge", output)
        self.assertEqual(Path("backlog.md").read_text(), original)

    def test_ambiguous_rows_are_reported_and_not_changed(self):
        original = "notes\n- [?] mystery #mystery\n- [ ] #emptyId\n* [ ] unsupported #otherId\n- [] malformed\n"
        self.write_backlog(original)
        code, output = self.doctor("--fix", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("unknown status", output)
        self.assertIn("empty task description", output)
        self.assertEqual(output.count("malformed task row"), 2)
        self.assertEqual(Path("backlog.md").read_text(), original)

    def test_conflict_markers_block_all_backlog_repairs(self):
        original = "<<<<<<< HEAD\n- [ ] first\n=======\n- [ ] other\n>>>>>>> branch\n"
        self.write_backlog(original)
        code, output = self.doctor("--fix", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("merge conflict marker", output)
        self.assertNotIn("Proposed backlog repairs", output)
        self.assertEqual(Path("backlog.md").read_text(), original)

    def test_declining_confirmation_or_eof_leaves_backlog_unchanged(self):
        original = "- [ ] legacy\n"
        self.write_backlog(original)
        for answer in ("n", "", EOFError()):
            kwargs = {"side_effect": answer} if isinstance(answer, Exception) else {"return_value": answer}
            with self.subTest(answer=answer), mock.patch.object(sys.stdin, "isatty", return_value=True), mock.patch(
                "builtins.input", **kwargs
            ):
                self.doctor("--fix")
            self.assertEqual(Path("backlog.md").read_text(), original)

    def test_confirmed_prompt_repairs_backlog_but_not_declined_setup(self):
        self.write_backlog("- [ ] legacy\n")
        Path("AGENTS.md").write_text("Project rules\n")
        with mock.patch.object(sys.stdin, "isatty", return_value=True), mock.patch(
            "builtins.input", side_effect=["yes", "n"]
        ) as prompt:
            code, output = self.doctor("--fix")
        self.assertEqual(code, 0, output)
        self.assertEqual(prompt.call_count, 2)
        self.assertRegex(Path("backlog.md").read_text(), r"- \[ \] legacy #[A-Za-z][A-Za-z0-9]{4,5}\n")
        self.assertEqual(Path("AGENTS.md").read_text(), "Project rules\n")

    def test_ambiguous_archive_rows_do_not_suggest_active_only_commands(self):
        self.write_backlog("---\n- [?] #ambiguous\n")
        code, output = self.doctor("--fix", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("explicit status decision", output)
        self.assertIn("explicit description decision", output)
        self.assertNotIn("bl edit", output)
        self.assertNotIn("bl done/untake", output)
        self.assertEqual(Path("backlog.md").read_text(), "---\n- [?] #ambiguous\n")

    def test_fix_requires_confirmation_in_noninteractive_session(self):
        original = "- [ ] legacy\n"
        self.write_backlog(original)
        with mock.patch.object(sys.stdin, "isatty", return_value=False), mock.patch("builtins.input") as prompt:
            _, output = self.doctor("--fix")
        prompt.assert_not_called()
        self.assertIn("--fix --yes", output)
        self.assertEqual(Path("backlog.md").read_text(), original)

    def test_repair_refuses_concurrent_changes_without_holding_prompt_lock(self):
        self.write_backlog("- [ ] legacy\n")
        updated = "- [ ] legacy\n- [>] concurrently claimed #freshId\n"

        def confirm(*_args):
            with bl.backlog_lock(timeout=0.1):
                self.write_backlog(updated)
            return True

        with mock.patch.object(bl, "_doctor_confirm", side_effect=confirm):
            code, output = self.doctor("--fix")
        self.assertEqual(code, 1)
        self.assertIn("Backlog changed during diagnosis", output)
        self.assertEqual(Path("backlog.md").read_text(), updated)

    def test_atomic_write_failure_preserves_original(self):
        original = "- [ ] legacy\n"
        self.write_backlog(original)
        with mock.patch.object(bl.os, "replace", side_effect=OSError("failure")):
            code, output = self.doctor("--fix", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("Cannot save", output)
        self.assertEqual(Path("backlog.md").read_text(), original)
        self.assertEqual(list(Path(".").glob(".backlog.*.tmp")), [])
        with bl.backlog_lock(timeout=0.1):
            pass

    def test_unreadable_backlog_does_not_skip_other_checks(self):
        Path("backlog.md").write_bytes(b"bad utf8 \xff")
        code, output = self.doctor()
        self.assertEqual(code, 1)
        self.assertIn("not valid UTF-8", output)
        for title in ("Agent instructions", "Version", "API keys"):
            self.assertIn(title, output)
        self.assertEqual(Path("backlog.md").read_bytes(), b"bad utf8 \xff")

    def test_hard_link_and_unwritable_backlogs_are_not_repaired(self):
        self.write_backlog("- [ ] legacy\n")
        os.link("backlog.md", "alias.md")
        code, output = self.doctor("--fix", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("hard-linked", output)
        Path("alias.md").unlink()
        with mock.patch.object(bl.os, "access", return_value=False):
            code, output = self.doctor("--fix", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("not writable", output)
        self.assertEqual(Path("backlog.md").read_text(), "- [ ] legacy\n")

    def test_lock_failure_still_checks_setup_and_keys(self):
        with mock.patch.object(bl, "backlog_lock", side_effect=bl.BacklogError("Timed out waiting")):
            code, output = self.doctor()
        self.assertEqual(code, 1)
        self.assertIn("Timed out waiting", output)
        self.assertIn("API keys", output)

    def test_cli_help_offline_and_invalid_flags(self):
        self.write_backlog("- [ ] normal #normal\n")
        result = self.run_bl("doctor", "--offline")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("IDs and statuses are valid", result.stdout)
        for args in (("--yes",), ("--unknown",), ("--fix", "--unknown")):
            result = self.run_bl("doctor", *args)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Usage: bl doctor", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
        help_result = self.run_bl("doctor", "--help")
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("--offline", help_result.stdout)
        self.assertIn("bl doctor", self.run_bl("help").stdout)


class DoctorSetupTests(helpers.BacklogTestCase):
    def test_current_missing_stale_and_duplicate_instructions(self):
        cases = [
            (bl.BL_INSTRUCTION + "\n", "OK"),
            ("project notes\n", "WARN"),
            ("Use `bl` to manage the local backlog.\n", "WARN"),
            ((bl.BL_INSTRUCTION + "\n") * 2, "WARN"),
        ]
        for text, expected in cases:
            with self.subTest(expected=expected, text=text[:30]):
                Path("AGENTS.md").write_text(text)
                issues = bl._doctor_setup()
                self.assertEqual(issues[0][0], expected)
                self.assertEqual(Path("AGENTS.md").read_text(), text)
        Path("AGENTS.md").unlink()
        self.assertIn("choose and create", bl._doctor_setup()[0][1])

    def test_fix_refreshes_symlinked_instructions_without_losing_project_text(self):
        text = "# Project\nKeep this rule.\n" + bl.BL_INSTRUCTION + "\nUse `bl` to manage old instructions.\n"
        Path("CLAUDE.md").write_text(text)
        Path("AGENTS.md").symlink_to("CLAUDE.md")
        with contextlib.redirect_stdout(io.StringIO()):
            code = bl.doctor(["--offline", "--fix", "--yes"])
        self.assertEqual(code, 0)
        self.assertTrue(Path("AGENTS.md").is_symlink())
        fixed = Path("CLAUDE.md").read_text()
        self.assertEqual(fixed, "# Project\nKeep this rule.\n" + bl.BL_INSTRUCTION + "\n")
        self.assertTrue(all(level == "OK" for level, _ in bl._doctor_setup()))

    def test_broken_symlink_and_bad_utf8_report_errors(self):
        Path("AGENTS.md").symlink_to("missing.md")
        Path("CLAUDE.md").write_bytes(b"\xff")
        issues = bl._doctor_setup()
        self.assertEqual([level for level, _ in issues], ["ERROR", "ERROR"])
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(bl, "setup") as setup:
            code = bl.doctor(["--offline", "--fix", "--yes"])
        self.assertEqual(code, 1)
        setup.assert_not_called()
        self.assertFalse(Path("missing.md").exists())

    def test_setup_write_failure_is_reported_without_traceback(self):
        Path("AGENTS.md").write_text("old instructions\n")
        output = io.StringIO()
        with mock.patch.object(bl, "setup", side_effect=PermissionError()), contextlib.redirect_stdout(output):
            code = bl.doctor(["--offline", "--fix", "--yes"])
        self.assertEqual(code, 1)
        self.assertIn("bl setup failed", output.getvalue())


class DoctorNetworkTests(helpers.BacklogTestCase):
    def test_github_compares_versions_numerically_without_executing_source(self):
        cases = [("2.10.0", "WARN", "is available"), ("2.1.0", "OK", "up to date"), ("2.0.0", "OK", "newer than")]
        for version, level, message in cases:
            with self.subTest(version=version):
                payload = f'VERSION = "{version}"\nraise RuntimeError("not executable")\n'.encode()
                opener = FakeOpener(FakeResponse(bl.REPO_RAW_URL, payload))
                with mock.patch.object(bl, "VERSION", "2.1.0"), mock.patch.object(bl, "_no_redirect_opener", return_value=opener):
                    issues = bl._doctor_update()
                self.assertEqual(issues[0][0], level)
                self.assertIn(message, issues[0][1])
                self.assertEqual(opener.timeout, bl.DOCTOR_TIMEOUT)
                self.assertNotIn("Authorization", dict(opener.request.header_items()))
                if level == "WARN":
                    self.assertIn("curl -fsSL", issues[0][1])

    def test_invalid_update_responses_are_unknown_not_up_to_date(self):
        responses = [
            FakeResponse(bl.REPO_RAW_URL, b"not a version"),
            FakeResponse(bl.REPO_RAW_URL, b"\xff"),
            FakeResponse(bl.REPO_RAW_URL, b"x" * (bl.MAX_UPDATE_RESPONSE + 1)),
            FakeResponse("https://evil.invalid", b'VERSION = "99.0.0"'),
            FakeResponse(bl.REPO_RAW_URL, b'VERSION = "99.0.0"', status=503),
        ]
        for response in responses:
            with self.subTest(url=response.endpoint, status=response.status), mock.patch.object(
                bl, "_no_redirect_opener", return_value=FakeOpener(response)
            ):
                issues = bl._doctor_update()
            self.assertEqual(issues[0][0], "WARN")
            self.assertIn("unknown", issues[0][1])

    def test_key_probes_generate_bounded_ids_without_backlog_or_secret_output(self):
        environments = {"OPENAI_API_KEY": "openai-secret", "ANTHROPIC_API_KEY": "claude-secret"}
        self.write_backlog("- [ ] private-backlog-content #secretTask\n")
        responses = {
            "openai": {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "probe1"}]}]},
            "anthropic": {"content": [{"type": "text", "text": "probe1"}]},
        }
        for provider, endpoint, header, secret, model in (
            ("openai", bl.OPENAI_ENDPOINT, "Authorization", "Bearer openai-secret", bl.OPENAI_MODEL),
            ("anthropic", bl.ANTHROPIC_ENDPOINT, "X-api-key", "claude-secret", bl.ANTHROPIC_MODEL),
        ):
            with self.subTest(provider=provider):
                response = FakeResponse(endpoint, json.dumps(responses[provider]).encode())
                opener = FakeOpener(response)
                with mock.patch.dict(os.environ, environments), mock.patch.object(bl, "_no_redirect_opener", return_value=opener), mock.patch.object(
                    response, "read", wraps=response.read
                ) as read:
                    issues = bl._doctor_key(provider)
                self.assertEqual(issues[0][0], "OK")
                self.assertIn("generation", issues[0][1])
                self.assertIn("quota usable now", issues[0][1])
                self.assertIn(model, issues[0][1])
                self.assertEqual(opener.request.get_method(), "POST")
                payload = json.loads(opener.request.data)
                self.assertEqual(payload["model"], model)
                self.assertNotIn("private-backlog-content", str(payload))
                self.assertNotIn("secretTask", str(payload))
                self.assertEqual(opener.request.get_header(header), secret)
                self.assertEqual(opener.timeout, bl.PROVIDER_TIMEOUT)
                read.assert_called_once_with(bl.MAX_PROVIDER_RESPONSE + 1)
                for private in ("openai-secret", "claude-secret", "probe1"):
                    self.assertNotIn(private, str(issues))
                if provider == "anthropic":
                    self.assertIsNone(opener.request.get_header("Authorization"))
                    self.assertEqual(payload["max_tokens"], 16)
                    self.assertEqual(payload["system"], bl.ID_GENERATION_PROMPT)
                    self.assertIn("3–8 character ASCII ID", payload["system"])
                    self.assertIn("SCORE → SCR", payload["system"])
                    self.assertEqual(payload["messages"][-1]["content"], bl.DOCTOR_PROBE_DESCRIPTION)
                else:
                    self.assertIsNone(opener.request.get_header("X-api-key"))
                    self.assertEqual(payload["max_output_tokens"], 16)
                    self.assertFalse(payload["store"])
                    self.assertEqual(payload["input"][0]["content"], bl.ID_GENERATION_PROMPT)
                    self.assertIn("3–8 character ASCII ID", payload["input"][0]["content"])
                    self.assertIn("SCORE → SCR", payload["input"][0]["content"])
                    self.assertEqual(payload["input"][-1]["content"], bl.DOCTOR_PROBE_DESCRIPTION)

    def test_provider_generation_normalizes_keys_like_doctor(self):
        response = FakeResponse(bl.ANTHROPIC_ENDPOINT, b'{"content":[{"type":"text","text":"valid1"}]}')
        opener = FakeOpener(response)
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "  secret-key \n"}), mock.patch.object(
            bl, "_no_redirect_opener", return_value=opener
        ):
            self.assertEqual(bl._fetch_provider_candidate("anthropic", "task"), "valid1")
        self.assertEqual(opener.request.get_header("X-api-key"), "secret-key")

    def test_http_errors_distinguish_invalid_denied_rate_limit_and_outage(self):
        for provider in ("openai", "anthropic"):
            for code, level, message in ((401, "ERROR", "rejected"), (403, "ERROR", "denied"), (429, "WARN", "could not be verified"), (500, "WARN", "could not be verified"), (302, "WARN", "could not be verified")):
                with self.subTest(provider=provider, code=code):
                    body = io.BytesIO(b"sensitive-body")
                    error = urllib.error.HTTPError("https://api.invalid", code, "secret-error", {}, body)
                    opener = mock.Mock()
                    opener.open.side_effect = error
                    with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "secret-key", "ANTHROPIC_API_KEY": "secret-key"}), mock.patch.object(
                        bl, "_no_redirect_opener", return_value=opener
                    ):
                        issues = bl._doctor_key(provider)
                    self.assertEqual(issues[0][0], level)
                    self.assertIn(message, issues[0][1])
                    self.assertIn(f"HTTP {code}", issues[0][1])
                    for secret in ("sensitive-body", "secret-error", "secret-key"):
                        self.assertNotIn(secret, str(issues))
                    self.assertTrue(body.closed)

    def test_quota_exhaustion_is_reported_as_error_with_no_retry(self):
        cases = [
            ("openai", 429, {"type": "insufficient_quota", "code": "credit_balance_exhausted"}),
            ("openai", 429, {"code": "insufficient_quota"}),
            ("openai", 429, {"type": "insufficient_quota"}),
            ("openai", 400, {"code": "billing_hard_limit_reached"}),
            ("anthropic", 400, {"type": "invalid_request_error", "message": "Your credit balance is too low to access the Anthropic API."}),
            ("anthropic", 402, {"code": "insufficient_credits"}),
        ]
        for provider, code, detail in cases:
            with self.subTest(provider=provider, code=code, detail=detail):
                body = io.BytesIO(json.dumps({"error": {"private": "secret-body", **detail}}).encode())
                error = urllib.error.HTTPError("https://api.invalid", code, "secret-error", {}, body)
                opener = mock.Mock()
                opener.open.side_effect = error
                with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "secret-key", "ANTHROPIC_API_KEY": "secret-key"}), mock.patch.object(
                    bl, "_no_redirect_opener", return_value=opener
                ):
                    issues = bl._doctor_key(provider)
                self.assertEqual(issues[0][0], "ERROR")
                self.assertIn("insufficient balance or exhausted quota", issues[0][1])
                self.assertIn(f"HTTP {code}", issues[0][1])
                self.assertIn("top up credits", issues[0][1])
                self.assertEqual(opener.open.call_count, 1)
                self.assertTrue(body.closed)
                self.assertNotIn("secret", str(issues))

    def test_rate_limits_do_not_claim_empty_balance(self):
        for detail in ({"code": "rate_limit_exceeded"}, {"type": "rate_limit_error"}):
            body = io.BytesIO(json.dumps({"error": detail}).encode())
            error = urllib.error.HTTPError("https://api.invalid", 429, "limit", {}, body)
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "secret-key"}), mock.patch.object(
                bl, "_fetch_provider_candidate", side_effect=error
            ) as fetch:
                issues = bl._doctor_key("openai")
            self.assertEqual(issues[0][0], "WARN")
            self.assertIn("rate limited", issues[0][1])
            self.assertIn("balance/quota could not be verified", issues[0][1])
            self.assertNotIn("top up", issues[0][1])
            fetch.assert_called_once_with("openai", bl.DOCTOR_PROBE_DESCRIPTION)

    def test_unknown_malformed_and_oversized_429_bodies_are_not_assumed_quota(self):
        bodies = [
            b"not-json", b"[]", b'{"error":[]}', b'{"error":{"code":[],"type":{},"message":[]}}',
            b"x" * (bl.MAX_PROVIDER_RESPONSE + 1), b"\xff",
        ]
        for raw in bodies:
            with self.subTest(size=len(raw)):
                body = io.BytesIO(raw)
                error = urllib.error.HTTPError("https://api.invalid", 429, "secret-error", {}, body)
                with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "secret-key"}), mock.patch.object(
                    bl, "_fetch_provider_candidate", side_effect=error
                ), mock.patch.object(error, "read", wraps=error.read) as read:
                    issues = bl._doctor_key("openai")
                read.assert_called_once_with(bl.MAX_PROVIDER_RESPONSE + 1)
                self.assertEqual(issues[0][0], "WARN")
                self.assertIn("balance/quota could not be verified", issues[0][1])
                self.assertNotIn("top up", issues[0][1])
                self.assertNotIn("secret", str(issues))
                self.assertTrue(body.closed)

    def test_payment_model_and_error_body_read_failures_are_safe(self):
        for code, level, message in ((402, "ERROR", "payment required"), (404, "ERROR", "check model"), (400, "ERROR", "check model"), (429, "WARN", "could not be verified")):
            with self.subTest(code=code):
                body = io.BytesIO(b"secret-body")
                error = urllib.error.HTTPError("https://api.invalid", code, "secret-error", {}, body)
                with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "secret-key"}), mock.patch.object(
                    bl, "_fetch_provider_candidate", side_effect=error
                ), mock.patch.object(error, "read", side_effect=TimeoutError("secret-timeout")):
                    issues = bl._doctor_key("openai")
                self.assertEqual(issues[0][0], level)
                self.assertIn(message, issues[0][1])
                self.assertNotIn("secret", str(issues))
                self.assertTrue(body.closed)

    def test_exhausted_quota_makes_doctor_fail_and_explains_probe_cost(self):
        body = io.BytesIO(b'{"error":{"code":"credit_balance_exhausted"}}')
        error = urllib.error.HTTPError("https://api.invalid", 429, "secret", {}, body)
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "secret-key"}), mock.patch.object(
            bl, "_fetch_provider_candidate", side_effect=error
        ) as fetch, mock.patch.object(bl, "_doctor_update", return_value=[]), contextlib.redirect_stdout(output):
            code = bl.doctor([])
        self.assertEqual(code, 1)
        self.assertIn("HTTP 429", output.getvalue())
        self.assertIn("standard API charges may apply", output.getvalue())
        self.assertIn("never backlog content", output.getvalue())
        self.assertNotIn("secret", output.getvalue())
        fetch.assert_called_once_with("openai", bl.DOCTOR_PROBE_DESCRIPTION)
        help_text = self.run_bl("doctor", "--help").stdout
        self.assertIn("potentially billable", help_text)

    def test_smart_id_provider_does_not_switch_to_claude_on_failure(self):
        failure = mock.Mock(returncode=1, stdout="")
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "secret", "ANTHROPIC_API_KEY": "other"}), mock.patch.object(
            bl.subprocess, "run", return_value=failure
        ) as run:
            self.assertIsNone(bl._semantic_candidate("synthetic task"))
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][-1], "openai")

    def test_invalid_generated_id_is_not_misreported_as_network_failure(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "secret-key"}), mock.patch.object(
            bl, "_fetch_provider_candidate", side_effect=bl.InvalidProviderCandidate("private output")
        ) as fetch:
            issues = bl._doctor_key("openai")
        self.assertEqual(issues[0][0], "WARN")
        self.assertIn("API generation succeeded", issues[0][1])
        self.assertIn("valid 3–8 character ID", issues[0][1])
        self.assertIn("Key and connectivity are working", issues[0][1])
        self.assertNotIn("private output", str(issues))
        fetch.assert_called_once_with("openai", bl.DOCTOR_PROBE_DESCRIPTION)

    def test_successful_but_unusable_response_has_its_own_diagnosis(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "secret-key"}), mock.patch.object(
            bl, "_fetch_provider_candidate", side_effect=bl.InvalidProviderResponse("private body")
        ):
            issues = bl._doctor_key("openai")
        self.assertEqual(issues[0][0], "WARN")
        self.assertIn("API response could not be verified", issues[0][1])
        self.assertIn("model/API compatibility", issues[0][1])
        self.assertNotIn("private body", str(issues))

    def test_network_errors_are_redacted_and_do_not_abort_checks(self):
        failures = [TimeoutError("secret"), urllib.error.URLError("secret"), ValueError("secret"), bl.HTTPException("secret")]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                opener = mock.Mock()
                opener.open.side_effect = failure
                output = io.StringIO()
                with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "secret", "ANTHROPIC_API_KEY": "secret"}), mock.patch.object(
                    bl, "_no_redirect_opener", return_value=opener
                ), contextlib.redirect_stdout(output):
                    code = bl.doctor([])
                self.assertEqual(code, 0)
                self.assertEqual(opener.open.call_count, 3)
                self.assertNotIn("secret", output.getvalue())
                self.assertIn("update status is unknown", output.getvalue())
                self.assertEqual(output.getvalue().count("could not be verified"), 2)

    def test_missing_blank_and_offline_keys_never_make_network_requests(self):
        with mock.patch.object(bl, "_no_redirect_opener") as opener:
            for value in ("", " \t"):
                with mock.patch.dict(os.environ, {"OPENAI_API_KEY": value, "ANTHROPIC_API_KEY": value}):
                    for provider in ("openai", "anthropic"):
                        self.assertIn("not set", bl._doctor_key(provider)[0][1])
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "secret", "ANTHROPIC_API_KEY": "other"}), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bl.doctor(["--offline"]), 0)
            opener.assert_not_called()

    def test_redirects_are_not_followed_or_reported_as_valid(self):
        opener = bl._no_redirect_opener()
        handler = next(handler for handler in opener.handlers if isinstance(handler, bl.urllib.request.HTTPRedirectHandler))
        self.assertIsNone(handler.redirect_request(None, None, 302, "redirect", {}, "https://evil.invalid"))
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "secret"}), mock.patch.object(
            bl, "_no_redirect_opener", return_value=FakeOpener(FakeResponse("https://evil.invalid", b""))
        ):
            issues = bl._doctor_key("openai")
        self.assertEqual(issues[0][0], "WARN")
        self.assertIn("could not be verified", issues[0][1])

    def test_network_checks_run_outside_backlog_lock(self):
        self.write_backlog("- [ ] normal #normal\n")

        def update(_offline):
            with bl.backlog_lock(timeout=0.1):
                pass
            return [("OK", "test version")]

        with mock.patch.object(bl, "_doctor_update", side_effect=update), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bl.doctor([]), 0)

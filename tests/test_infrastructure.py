"""
Couverture des briques d'infrastructure : configuration, terminal, diagnostics du client Claude, client Git (cas
d'erreur), chemins d'échec de l'isolation, simulations, résumé du CLI et rapport d'audit.
"""

import builtins
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from gitrepo import make_repo
from test_claude_cli import make_fake_binary

from workflow_claude import cli, config
from workflow_claude.clients import git_client as git_module
from workflow_claude.clients.claude_cli import ClaudeCliClient, resolve_binary
from workflow_claude.clients.git_client import GitClient, GitClientError
from workflow_claude.clients.mocks import MOCK_MARKER_FILE, MockClaude
from workflow_claude.isolation import IsolatedRun, WorkflowPreconditionError
from workflow_claude.models import DevSpecialty, StepRecord, WorkflowExecutionReport, WorkflowType
from workflow_claude.orchestrator import MultiAgentOrchestrator
from workflow_claude.roles import Role
from workflow_claude.ui import terminal


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
class TestConfig(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name).resolve()

    def isolated_env(self, **extra):
        env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_BIN", "APPDATA", "LOCALAPPDATA")}
        env.update(extra)
        return mock.patch.dict(os.environ, env, clear=True)

    def test_explicit_claude_bin_wins(self):
        with self.isolated_env(CLAUDE_BIN="/opt/claude/bin/claude"):
            self.assertEqual(config._find_claude_binary(), "/opt/claude/bin/claude")

    def test_binary_found_on_the_path(self):
        with self.isolated_env(), mock.patch.object(config.shutil, "which", return_value="/usr/bin/claude"):
            self.assertEqual(config._find_claude_binary(), "/usr/bin/claude")

    def test_latest_versioned_install_directory_is_used(self):
        for version in ("1.0.0", "2.5.1"):
            exe = self.home / ".config" / "Claude" / "claude-code" / version / "claude"
            exe.parent.mkdir(parents=True)
            exe.write_text("#!/bin/sh\n", encoding="utf-8")
            exe.chmod(0o755)
        with self.isolated_env(), mock.patch.object(config.shutil, "which", return_value=None), \
                mock.patch.object(config.Path, "home", return_value=self.home):
            self.assertTrue(config._find_claude_binary().replace("\\", "/").endswith("2.5.1/claude"))

    def test_standard_location_is_found(self):
        exe = self.home / ".local" / "bin" / "claude"
        exe.parent.mkdir(parents=True)
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        exe.chmod(0o755)
        with self.isolated_env(), mock.patch.object(config.shutil, "which", return_value=None), \
                mock.patch.object(config.Path, "home", return_value=self.home):
            self.assertEqual(config._find_claude_binary(), str(exe))

    def test_fallback_is_the_bare_command_name(self):
        with self.isolated_env(), mock.patch.object(config.shutil, "which", return_value=None), \
                mock.patch.object(config.Path, "home", return_value=self.home), \
                mock.patch.object(config.Path, "is_file", return_value=False), \
                mock.patch.object(config.Path, "is_dir", return_value=False):
            self.assertEqual(config._find_claude_binary(), "claude")

    def test_env_bool_variants(self):
        for raw, expected in (("1", True), ("TRUE", True), (" yes ", True), ("on", True), ("0", False), ("False", False),
                              ("no", False), ("off", False), ("", False)):
            with mock.patch.dict(os.environ, {"X_FLAG_TEST": raw}):
                self.assertIs(config.env_bool("X_FLAG_TEST", not expected), expected, raw)
        with mock.patch.dict(os.environ, {"X_FLAG_TEST": "peut-être"}):
            self.assertIs(config.env_bool("X_FLAG_TEST", True), True)   # valeur inconnue : défaut
        os.environ.pop("X_FLAG_TEST", None)
        self.assertIs(config.env_bool("X_FLAG_TEST", True), True)

    def test_coerce_bool_is_strict(self):
        self.assertIs(config._coerce_bool(True, False), True)
        self.assertIs(config._coerce_bool("false", True), False)   # avant : bool("false") == True
        self.assertIs(config._coerce_bool("YES", False), True)
        self.assertIs(config._coerce_bool("n'importe quoi", True), True)
        self.assertIs(config._coerce_bool(42, False), False)
        self.assertIs(config._coerce_bool(None, True), True)

    def test_user_config_loading(self):
        with mock.patch.object(config, "USER_CONFIG_DIR", self.home):
            self.assertEqual(config.load_user_config(), {})                       # absent
            (self.home / "config.json").write_text('{"run_tests": false}', encoding="utf-8")
            self.assertEqual(config.load_user_config(), {"run_tests": False})
            (self.home / "config.json").write_text("{pas du json", encoding="utf-8")
            self.assertEqual(config.load_user_config(), {})                       # invalide
            (self.home / "config.json").write_text("[1, 2]", encoding="utf-8")
            self.assertEqual(config.load_user_config(), {})                       # pas un objet

    def test_env_file_loading_never_overrides_existing_variables(self):
        env_file = self.home / ".env"
        env_file.write_text("TEST_ENVFILE_NEW=nouvelle\nTEST_ENVFILE_OLD=ecrasee\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"TEST_ENVFILE_OLD": "gardee"}):
            os.environ.pop("TEST_ENVFILE_NEW", None)
            config._load_env_file(env_file)
            self.assertEqual(os.environ["TEST_ENVFILE_NEW"], "nouvelle")
            self.assertEqual(os.environ["TEST_ENVFILE_OLD"], "gardee")
            os.environ.pop("TEST_ENVFILE_NEW", None)

    def test_env_file_fallback_parser_without_python_dotenv(self):
        env_file = self.home / ".env"
        env_file.write_text("# commentaire\n\nTEST_FALLBACK_A='quoted value'\nTEST_FALLBACK_B=\"double\"\nligne sans egal\n", encoding="utf-8")
        with mock.patch.dict(sys.modules, {"dotenv": None}), mock.patch.dict(os.environ, {}):
            config._load_env_file(env_file)
            self.assertEqual(os.environ["TEST_FALLBACK_A"], "quoted value")
            self.assertEqual(os.environ["TEST_FALLBACK_B"], "double")
            os.environ.pop("TEST_FALLBACK_A", None)
            os.environ.pop("TEST_FALLBACK_B", None)
        config._load_env_file(self.home / "inexistant.env")   # ne lève pas

    def test_env_search_order_is_cwd_then_user_directory(self):
        paths = config._env_search_paths()
        self.assertEqual(paths[0], Path.cwd() / ".env")
        self.assertEqual(paths[1], config.USER_CONFIG_DIR / ".env")


# ---------------------------------------------------------------------------
# Terminal
# ---------------------------------------------------------------------------
class Tty(io.StringIO):
    def isatty(self):
        return True


class TestTerminal(unittest.TestCase):
    def setUp(self):
        self.addCleanup(terminal.set_color_enabled, True)

    def test_interactive_spinner_animates_then_reports_success(self):
        out = Tty()
        with mock.patch.object(terminal, "is_ansi_supported", return_value=True), contextlib.redirect_stdout(out):
            with terminal.Spinner("Travail en cours"):
                time.sleep(0.25)
        text = out.getvalue()
        self.assertIn("Travail en cours", text)
        self.assertIn("✔", text)

    def test_interactive_spinner_reports_failure(self):
        out = Tty()
        with mock.patch.object(terminal, "is_ansi_supported", return_value=True), contextlib.redirect_stdout(out):
            with self.assertRaises(RuntimeError):
                with terminal.Spinner("Étape qui échoue"):
                    raise RuntimeError("boom")
        self.assertIn("ÉCHEC", out.getvalue())

    def test_non_interactive_spinner_prints_one_line_and_stop_is_safe(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            spinner = terminal.Spinner("Étape")
            spinner.start()
            spinner.stop()   # sans effet : rien ne tourne
        self.assertEqual(out.getvalue().count("Étape"), 1)

    def test_safe_print_survives_a_stream_that_cannot_encode_emojis(self):
        stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
        with contextlib.redirect_stdout(stream):
            terminal._safe_print("⏳ étape")   # ne lève pas
            stream.flush()
        self.assertIn("?", stream.buffer.getvalue().decode("ascii"))

    def test_colored_diff_when_supported(self):
        diff = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-ancien\n+nouveau\n contexte"
        with mock.patch.object(terminal, "is_ansi_supported", return_value=True):
            colored = terminal.format_colored_diff(diff)
        self.assertIn(terminal.GREEN + "+nouveau", colored)
        self.assertIn(terminal.RED + "-ancien", colored)
        self.assertIn(terminal.CYAN + "@@ -1 +1 @@", colored)
        self.assertIn("contexte", colored)
        with mock.patch.object(terminal, "is_ansi_supported", return_value=False):
            self.assertEqual(terminal.format_colored_diff(diff), diff)
        self.assertEqual(terminal.format_colored_diff(""), "")

    def test_confirm_action_non_interactive_returns_the_default(self):
        with mock.patch.object(sys, "stdin", io.StringIO()):
            self.assertTrue(terminal.confirm_action("ok ?", default=True))
            self.assertFalse(terminal.confirm_action("ok ?", default=False))

    def test_confirm_action_interactive_answers(self):
        with mock.patch.object(sys, "stdin", Tty()):
            for reply, default, expected in (("y", False, True), ("oui", False, True), ("O", False, True), ("n", True, False),
                                             ("", True, True), ("", False, False), ("peut-être", True, False)):
                with mock.patch.object(builtins, "input", return_value=reply):
                    self.assertIs(terminal.confirm_action("ok ?", default), expected, reply)
            with mock.patch.object(builtins, "input", side_effect=EOFError), contextlib.redirect_stdout(io.StringIO()):
                self.assertIs(terminal.confirm_action("ok ?", True), True)
            with mock.patch.object(builtins, "input", side_effect=KeyboardInterrupt), contextlib.redirect_stdout(io.StringIO()):
                self.assertIs(terminal.confirm_action("ok ?", False), False)

    def test_windows_vt_helper_always_returns_a_bool(self):
        self.assertIsInstance(terminal._enable_windows_vt(), bool)


# ---------------------------------------------------------------------------
# Client Claude : diagnostics et analyse de la sortie
# ---------------------------------------------------------------------------
class TestClaudeDiagnostics(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.client = ClaudeCliClient(binary_path=make_fake_binary(Path(self._tmp.name)), mock_mode=False)

    def test_supported_flags(self):
        flags = self.client.supported_flags(["--tools", "--permission-mode", "--option-inexistante"])
        self.assertEqual(flags, {"--tools": True, "--permission-mode": True, "--option-inexistante": False})

    def test_flags_are_all_false_when_the_binary_is_missing(self):
        client = ClaudeCliClient(binary_path=str(Path(self._tmp.name) / "absent"), mock_mode=False)
        self.assertEqual(client.supported_flags(["--tools"]), {"--tools": False})
        self.assertIsNone(client.auth_status())
        self.assertIsNone(client.version())

    def test_auth_status(self):
        self.assertEqual(self.client.auth_status(), {"loggedIn": True, "authMethod": "claude.ai"})
        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_LOGGED_IN": "0"}):
            self.assertFalse(self.client.auth_status()["loggedIn"])

    def test_auth_status_with_unreadable_output(self):
        proc = subprocess.CompletedProcess([], 0, stdout="pas du json", stderr="")
        with mock.patch.object(self.client, "_diagnostic_run", return_value=proc):
            self.assertIsNone(self.client.auth_status())
        proc = subprocess.CompletedProcess([], 0, stdout="[1, 2]", stderr="")
        with mock.patch.object(self.client, "_diagnostic_run", return_value=proc):
            self.assertIsNone(self.client.auth_status())

    def test_diagnostics_never_read_the_parents_stdin(self):
        with mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run:
            self.client.version()
        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_resolve_binary_on_posix_style_paths(self):
        self.assertEqual(resolve_binary("/usr/bin/claude"), ["/usr/bin/claude"])


class TestClaudeOutputParsing(unittest.TestCase):
    def setUp(self):
        self.client = ClaudeCliClient(mock_mode=False)

    def test_json_embedded_in_noise_on_a_single_line(self):
        raw = 'bruit de démarrage {"type":"result","is_error":false,"result":"ok { avec accolades }"} fin de ligne'
        self.assertEqual(self.client._parse_json(raw)["result"], "ok { avec accolades }")

    def test_json_on_its_own_line_after_logs(self):
        raw = 'log 1\nlog 2\n{"result": "dernier"}\n'
        self.assertEqual(self.client._extract_result(raw), "dernier")

    def test_no_json_returns_none_and_raw_text(self):
        self.assertIsNone(self.client._parse_json("rien d'exploitable {pas json"))
        self.assertIsNone(self.client._parse_json("   "))
        self.assertEqual(self.client._extract_result("texte brut"), "texte brut")

    def test_nested_object_that_is_not_a_result_is_skipped(self):
        raw = '{"meta": {"a": 1}} puis {"result": "bon"}'
        self.assertEqual(self.client._parse_json(raw)["result"], "bon")

    def test_result_with_missing_optional_fields(self):
        result = ClaudeCliClient._to_result({"result": "x"})
        self.assertEqual((result.text, result.is_error, result.cost_usd, result.permission_denials), ("x", False, None, []))
        result = ClaudeCliClient._to_result({"text": " t ", "api_error_status": "pas un entier", "permission_denials": "?"})
        self.assertEqual((result.text, result.api_error_status, result.permission_denials), ("t", None, []))

    def test_mock_mode_run_returns_text_and_clears_last_result(self):
        client = ClaudeCliClient(mock_mode=True)
        self.assertIn("ANALYSE QUALITÉ", client.run("x", "sonnet", role=Role.QUALITY))
        self.assertIsNone(client.last_result)


# ---------------------------------------------------------------------------
# Client Git : cas d'erreur
# ---------------------------------------------------------------------------
class TestGitClientErrors(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        self.addCleanup(self.repo.cleanup)
        self.git = GitClient(str(self.repo.path))
        self._plain = tempfile.TemporaryDirectory()
        self.addCleanup(self._plain.cleanup)
        self.plain = GitClient(self._plain.name)

    def test_outside_a_repository(self):
        self.assertFalse(self.plain.is_git_repository())
        self.assertFalse(self.plain.has_commits())
        self.assertEqual(self.plain.get_head_commit(), "N/A")
        self.assertEqual(self.plain.get_current_branch(), "HEAD")
        self.assertEqual(self.plain.get_modified_files(), [])
        self.assertEqual(self.plain.untracked_files(), [])
        with self.assertRaises(GitClientError):
            self.plain.rollback()

    def test_missing_git_binary_is_a_clear_error(self):
        with mock.patch.object(git_module.subprocess, "run", side_effect=FileNotFoundError):
            with self.assertRaises(GitClientError) as ctx:
                GitClient._exec(["status"], Path("."))
            self.assertIn("introuvable", str(ctx.exception))
            self.assertFalse(self.git.is_git_repository())

    def test_commit_with_nothing_to_commit_raises(self):
        with self.assertRaises(GitClientError):
            self.git.commit("chore: rien")

    def test_empty_repository_diff_falls_back_to_plain_diff(self):
        empty = make_repo(commit=False)
        self.addCleanup(empty.cleanup)
        empty.write("a.txt", "a\n")
        self.assertIsInstance(GitClient(str(empty.path)).get_diff(), str)

    def test_branch_helpers(self):
        self.assertFalse(self.git.delete_branch("n-existe-pas"))
        self.git.create_and_checkout_branch("feature")
        self.repo.write("a.txt", "a\n")
        self.git.commit("feat: a")
        self.git.checkout_branch("main")
        self.assertFalse(self.git.delete_branch("feature"))            # non fusionnée : refus sans -D
        self.assertTrue(self.git.delete_branch("feature", force=True))
        self.assertEqual(self.git.count_commits_ahead("HEAD", "branche-inconnue"), 0)

    def test_checkout_detached_and_successful_merge(self):
        sha = self.repo.git("rev-parse", "HEAD")
        self.git.checkout_detached(sha)
        self.assertEqual(self.git.get_head_ref(), (None, sha))
        self.git.checkout_branch("main")
        self.git.create_and_checkout_branch("feature")
        self.repo.write("b.txt", "b\n")
        self.git.commit("feat: b")
        self.git.checkout_branch("main")
        self.assertTrue(self.git.merge_branch("feature"))
        self.assertTrue((self.repo.path / "b.txt").exists())

    def test_stash_pop_without_a_message_pops_the_most_recent_entry(self):
        self.assertFalse(self.git.stash_pop())                       # pile vide
        self.repo.write("wip.txt", "x\n")
        self.assertTrue(self.git.stash_push("premier"))
        self.assertTrue(self.git.stash_pop())
        self.assertTrue((self.repo.path / "wip.txt").exists())

    def test_removing_many_untracked_files_in_chunks(self):
        for i in range(120):
            self.repo.write(f"junk/f{i}.tmp", "x\n")
        paths = self.git.untracked_files()
        self.assertEqual(len(paths), 120)
        self.git.remove_untracked([])                                  # sans effet
        self.git.remove_untracked(paths)
        self.assertEqual(self.git.untracked_files(), [])

    def test_exclude_locally_edge_cases(self):
        self.assertFalse(self.git.exclude_locally(""))
        self.assertFalse(self.git.exclude_locally("/"))
        self.assertTrue(self.git.exclude_locally("out"))
        self.assertFalse(self.git.exclude_locally("out"))               # déjà ignoré
        self.assertTrue(self.git.is_ignored("out/x.txt"))


# ---------------------------------------------------------------------------
# Isolation : chemins d'échec
# ---------------------------------------------------------------------------
class TestIsolationFailurePaths(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        self.addCleanup(self.repo.cleanup)
        self.git = GitClient(str(self.repo.path))
        self.repo.write("wip.txt", "travail en cours\n")

    def test_failed_stash_aborts_before_touching_anything(self):
        with mock.patch.object(GitClient, "stash_push", return_value=False):
            with self.assertRaises(WorkflowPreconditionError):
                with IsolatedRun(self.git):
                    self.fail("ne doit pas démarrer")
        self.assertEqual(self.repo.read("wip.txt"), "travail en cours\n")
        self.assertEqual(self.repo.current_branch(), "main")
        self.assertEqual(self.repo.stashes(), [])

    def test_branch_creation_failure_restores_the_stash_immediately(self):
        with mock.patch.object(GitClient, "create_and_checkout_branch", side_effect=GitClientError("refusé")):
            with self.assertRaises(GitClientError):
                with IsolatedRun(self.git, use_branch=True):
                    self.fail("ne doit pas démarrer")
        self.assertEqual(self.repo.read("wip.txt"), "travail en cours\n")
        self.assertEqual(self.repo.stashes(), [])

    def test_abort_survives_a_failing_rollback_and_still_restores_the_stash(self):
        with mock.patch.object(GitClient, "rollback", side_effect=GitClientError("rollback impossible")):
            with self.assertLogs("isolation", level="ERROR") as logs:
                with self.assertRaises(RuntimeError):
                    with IsolatedRun(self.git, use_branch=True):
                        raise RuntimeError("échec du workflow")
        self.assertIn("Rollback impossible", "\n".join(logs.output))
        self.assertEqual(self.repo.read("wip.txt"), "travail en cours\n")   # le stash est restauré quand même

    def test_abort_survives_a_failing_checkout(self):
        with mock.patch.object(GitClient, "checkout_branch", side_effect=GitClientError("checkout impossible")):
            with self.assertLogs("isolation", level="ERROR") as logs:
                with self.assertRaises(RuntimeError):
                    with IsolatedRun(self.git, use_branch=True):
                        raise RuntimeError("échec")
        self.assertIn("Impossible de revenir", "\n".join(logs.output))

    def test_leave_deletes_an_empty_branch_and_keeps_a_branch_with_commits(self):
        with IsolatedRun(self.git, use_branch=True) as guard:
            name = guard.work_branch
            guard.leave()
        self.assertNotIn(name, self.repo.branches())

        with IsolatedRun(self.git, use_branch=True) as guard:
            name = guard.work_branch
            self.repo.write("feature.txt", "f\n")
            guard.commit("feat: f")
            guard.leave(merge=False)
        self.assertIn(name, self.repo.branches())
        self.assertTrue(guard.branch_kept)

    def test_failed_merge_keeps_the_branch(self):
        with IsolatedRun(self.git, use_branch=True) as guard:
            self.repo.write("feature.txt", "f\n")
            guard.commit("feat: f")
            with mock.patch.object(GitClient, "merge_branch", return_value=False):
                with self.assertLogs("isolation", level="WARNING"):
                    guard.leave(merge=True)
        self.assertFalse(guard.merged)
        self.assertTrue(guard.branch_kept)


# ---------------------------------------------------------------------------
# Simulations
# ---------------------------------------------------------------------------
class TestMockBranches(unittest.TestCase):
    def test_dev_in_place_edge_cases(self):
        mock_claude = MockClaude(edit_files=True)
        self.assertIn("avec succès dans le projet",
                      mock_claude.respond("applique directement les modifications", "m", role=Role.DEV, permission_mode="acceptEdits"))
        with tempfile.TemporaryDirectory() as empty:
            out = mock_claude.respond("applique directement les modifications", "m", role=Role.DEV,
                                      cwd=empty, permission_mode="acceptEdits")
            self.assertIn("avec succès dans le projet", out)

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "util.py").write_text("def util():\n    return 1\n", encoding="utf-8")
            mock_claude.respond("applique directement les modifications", "m", role=Role.DEV, cwd=tmp, permission_mode="acceptEdits")
            self.assertIn("sortItems", Path(tmp, "util.py").read_text(encoding="utf-8"))   # fichier sans « sort » : ajout

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "algo.py").write_text("result = sort(values)\n", encoding="utf-8")
            mock_claude.respond("applique directement les modifications", "m", role=Role.DEV, cwd=tmp, permission_mode="acceptEdits")
            self.assertIn("in-situ edit", Path(tmp, "algo.py").read_text(encoding="utf-8"))

    def test_spec_target_selection_fallbacks(self):
        mock_claude = MockClaude()
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "notes.txt").write_text("x", encoding="utf-8")
            Path(tmp, "main.py").write_text("x", encoding="utf-8")
            self.assertIn("`main.py`", mock_claude.respond("explore le codebase", "m", role=Role.SPEC, cwd=tmp, tools="Read"))
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "readme.txt").write_text("x", encoding="utf-8")
            self.assertIn("`readme.txt`", mock_claude.respond("explore le codebase", "m", role=Role.SPEC, cwd=tmp, tools="Read"))
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "TabZ.tsx").write_text("x", encoding="utf-8")
            self.assertIn("`TabZ.tsx`", mock_claude.respond("explore l'onglet x", "m", role=Role.SPEC, cwd=tmp, tools="Read"))
        self.assertIn("TabX.tsx", mock_claude.respond("explore le codebase", "m", role=Role.SPEC, tools="Read"))

    def test_marker_mode_appends_to_an_existing_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker_mock = MockClaude(edit_files=False)
            for _ in range(2):
                marker_mock.respond("applique directement", "m", role=Role.DEV, cwd=tmp, permission_mode="acceptEdits")
            self.assertEqual(Path(tmp, MOCK_MARKER_FILE).read_text(encoding="utf-8").count("modification simulée"), 2)

    def test_unknown_role_gets_a_generic_answer(self):
        self.assertIn("Simulation", MockClaude().respond("bonjour", "haiku", role=None))


# ---------------------------------------------------------------------------
# Résumé du CLI et rapport d'audit
# ---------------------------------------------------------------------------
def summary_of(report, workspace=""):
    args = mock.Mock(workspace=workspace)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.print_summary(report, args)
    return out.getvalue()


class TestSummaryAndAudit(unittest.TestCase):
    def full_report(self):
        report = WorkflowExecutionReport(prompt_simple="Ajoute un tri", is_success=True, is_in_repo=True)
        report.workflow_type, report.dev_specialty = WorkflowType.MOYENNE, DevSpecialty.UI
        report.project_dir, report.original_branch, report.branch_name = "/p", "main", "workflow/ai-1"
        report.modified_files, report.git_diff = ["src/a.py"], "+x = 1\n"
        report.tests_passed, report.baseline_tests_passed = True, False
        report.baseline_tests_failed, report.tests_failed = ["t::legacy"], []
        report.doc_files_kept, report.doc_files_reverted = ["README.md"], ["src/evil.py"]
        report.commit_hash, report.cost_usd = "abc1234", 0.1234
        report.jev_mode = "mock"
        report.decisions = [
            {"step": "ROUTAGE_COMPLEXITE", "kind": "choice", "result": "Tâche Moyenne", "value": 0.9, "latency_ms": 250},
            {"step": "VALIDATION_QUALITE_CYCLE_1", "kind": "noul", "result": True, "value": 0.77, "latency_ms": None},
        ]
        report.history = [StepRecord("DEV_IN_SITU_CYCLE_1", "sonnet", "p", "o", 1.5, {"cost_usd": 0.05})]
        return report

    def test_in_repo_success_with_branch_kept(self):
        text = summary_of(self.full_report(), workspace="out")
        for expected in ("WORKFLOW TERMINÉ AVEC SUCCÈS", "Tâche Moyenne", "Dev UI", "0.1234 USD", "SIMULATION",
                         "Décisions Jev", "✅ VALIDATION_QUALITE_CYCLE_1", "Doc mise à jour  : README.md",
                         "Hors doc annulé  : src/evil.py", "Commit Git créé  : [abc1234]", "git merge workflow/ai-1",
                         "Suite de tests   : ✅", "Rapports (audit"):
            self.assertIn(expected, text)

    def test_merged_and_detached_head_and_stash_conflict_messages(self):
        report = self.full_report()
        report.merged = True
        self.assertIn("Fusion réussie dans 'main'", summary_of(report))

        report = self.full_report()
        report.original_branch = "HEAD"
        self.assertIn("depuis la branche de votre choix", summary_of(report))

        report = self.full_report()
        report.stash_restored = False
        self.assertIn("git stash pop", summary_of(report))

    def test_failed_standalone_run_shows_the_error_and_the_rejected_code(self):
        report = WorkflowExecutionReport(prompt_simple="x", error_message="Circuit breaker : ...", code_produit="print('x')\n" * 100)
        text = summary_of(report)
        self.assertIn("WORKFLOW INCOMPLET : Circuit breaker", text)
        self.assertIn("CODE PRODUIT", text)
        self.assertIn("tronqué pour affichage", text)

    def test_step_icons(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            for name in ("DEV_CYCLE_1", "TESTS_FAILED_CYCLE_1", "DOC_EDIT", "AUTRE_ETAPE"):
                cli.print_step(StepRecord(name, "sonnet", "", "", 0.5))
        text = out.getvalue()
        self.assertIn("💻 [DEV_CYCLE_1]", text)
        self.assertIn("🧪❌ [TESTS_FAILED_CYCLE_1]", text)
        self.assertIn("📚 [DOC_EDIT]", text)
        self.assertIn("⚙️ [AUTRE_ETAPE]", text)

    def test_ask_merge_assume_yes_and_prompt(self):
        report = self.full_report()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(cli.ask_merge(report, assume_yes=True))
            with mock.patch.object(cli, "confirm_action", return_value=False) as confirm:
                self.assertFalse(cli.ask_merge(report))
        self.assertIn("workflow/ai-1", confirm.call_args.args[0])

    def test_audit_markdown_content(self):
        text = MultiAgentOrchestrator._audit_markdown(self.full_report())
        for expected in ("**Statut :** SUCCÈS", "SIMULATION", "0.1234 USD", "Tests (baseline) :** déjà en échec : t::legacy",
                         "Tests (dernier cycle) :** verts", "Documentation mise à jour :** README.md",
                         "Modifications hors documentation annulées :** src/evil.py", "`abc1234`",
                         "Branche conservée :** `workflow/ai-1`", "ROUTAGE_COMPLEXITE** : Tâche Moyenne (0.90), 250 ms",
                         "DEV_IN_SITU_CYCLE_1** (sonnet, 1.50s, 0.0500 USD)"):
            self.assertIn(expected, text)

        report = self.full_report()
        report.is_success, report.error_message = False, "boom"
        report.merged, report.stash_restored = True, False
        failed = MultiAgentOrchestrator._audit_markdown(report)
        self.assertIn("ÉCHEC — boom", failed)
        self.assertIn("**Fusion :** dans `main`", failed)
        self.assertIn("ATTENTION", failed)


if __name__ == "__main__":
    unittest.main()

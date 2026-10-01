"""
Phase 7 : ergonomie du CLI (version, prompt obligatoire, validation du dossier projet, --json, --log-file, --no-color,
options booléennes) et rapports écrits quel que soit le résultat (succès, échec, exception).
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gitrepo import make_repo

from workflow_claude import __version__, cli
from workflow_claude.clients.claude_cli import ClaudeCliClient, ClaudeQuotaError
from workflow_claude.clients.jev_client import JevApiError, JevClient
from workflow_claude.models import DevSpecialty, WorkflowExecutionReport, WorkflowType
from workflow_claude.orchestrator import MultiAgentOrchestrator
from workflow_claude.ui import terminal

PROMPT = "Modifie la fonction de tri dans l'onglet x"


class UxTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.workspace = str(self.tmp / "ws")
        self.addCleanup(terminal.set_color_enabled, True)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def mock_run(self, *extra, prompt="Crée un module"):
        return self.run_cli("--mock", "--standalone", "--workspace", self.workspace, *extra, prompt)


class TestUsage(UxTestCase):
    def test_version(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertEqual(out.getvalue().strip(), f"workflow {__version__}")

    def test_prompt_is_required_and_nothing_is_launched(self):
        """Avant : `workflow` sans argument lançait un vrai run avec un prompt de démonstration."""
        with mock.patch.object(cli, "MultiAgentOrchestrator") as orchestrator:
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as ctx:
                    cli.main(["--mock"])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("un prompt est requis", err.getvalue())
        orchestrator.assert_not_called()

    def test_boolean_options_have_both_forms(self):
        parser = cli.build_parser()
        defaults = parser.parse_args(["x"])
        self.assertTrue(defaults.branch)
        self.assertFalse(defaults.allow_bash)
        self.assertTrue(defaults.run_tests)
        self.assertTrue(defaults.doc_edit)

        args = parser.parse_args(["--no-branch", "--no-doc-edit", "--allow-bash", "--no-tests", "x"])
        self.assertEqual((args.branch, args.doc_edit, args.allow_bash, args.run_tests), (False, False, True, False))
        self.assertFalse(parser.parse_args(["--no-run-tests", "x"]).run_tests)
        self.assertFalse(parser.parse_args(["--no-allow-bash", "x"]).allow_bash)
        self.assertTrue(parser.parse_args(["--branch", "x"]).branch)


class TestProjectDirValidation(UxTestCase):
    def test_missing_directory_is_an_error(self):
        code, _, err = self.run_cli("--mock", "--project-dir", str(self.tmp / "absent"), "--workspace", self.workspace, "x")
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("introuvable", err)

    def test_non_git_directory_is_an_error_instead_of_a_silent_standalone_run(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        with mock.patch.object(cli, "MultiAgentOrchestrator") as orchestrator:
            code, _, err = self.run_cli("--mock", "--project-dir", str(plain), "--workspace", self.workspace, "x")
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("n'est pas un dépôt Git", err)
        self.assertIn("--standalone", err)
        orchestrator.assert_not_called()

    def test_standalone_flag_bypasses_the_check(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        code, out, _ = self.run_cli("--mock", "--standalone", "--project-dir", str(plain), "--workspace", self.workspace, "x")
        self.assertEqual(code, 0)
        self.assertIn("Standalone forcé", out)

    def test_git_repository_is_accepted(self):
        repo = make_repo()
        self.addCleanup(repo.cleanup)
        code, out, _ = self.run_cli("--mock", "--yes", "--project-dir", str(repo.path), "--workspace", self.workspace, "x")
        self.assertEqual(code, 0)
        self.assertIn("In-Repo :", out)

    def test_no_explicit_project_dir_outside_git_is_announced_as_standalone(self):
        with mock.patch.object(cli.GitClient, "is_git_repository", return_value=False):
            args = cli.build_parser().parse_args(["x"])
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cli.print_banner(args)
        self.assertIn("le dossier courant n'est pas un dépôt Git", out.getvalue())


class TestJsonOutput(UxTestCase):
    def test_success_prints_only_json_on_stdout_and_the_human_output_on_stderr(self):
        code, out, err = self.mock_run("--json")
        self.assertEqual(code, 0)
        data = json.loads(out)                       # stdout = JSON pur
        self.assertTrue(data["is_success"])
        self.assertEqual(data["exit_code"], 0)
        self.assertEqual(data["jev_mode"], "mock")
        self.assertIn(data["workflow_type"], ("Tâche Simple", "Tâche Moyenne", "Tâche Complexe"))
        self.assertTrue(data["history"])
        self.assertNotIn("prompt_sent", data["history"][0])   # pas de contenu de prompt par défaut
        self.assertNotIn("code_produit", data)
        self.assertIn("ORCHESTRATEUR MULTI-AGENTS", err)      # l'affichage humain est passé sur stderr
        self.assertNotIn("ORCHESTRATEUR", out)

    def test_claude_error_is_reported_as_json_with_its_exit_code(self):
        with mock.patch.object(ClaudeCliClient, "run", side_effect=ClaudeQuotaError("limite atteinte, resets 5pm")):
            code, out, _ = self.mock_run("--json")
        data = json.loads(out)
        self.assertEqual(code, 3)
        self.assertEqual((data["is_success"], data["exit_code"]), (False, 3))
        self.assertIn("resets 5pm", data["error_message"])

    def test_early_errors_are_json_too(self):
        code, out, _ = self.run_cli("--mock", "--json", "--project-dir", str(self.tmp / "absent"), "x")
        data = json.loads(out)
        self.assertEqual((code, data["exit_code"]), (1, 1))
        self.assertIn("introuvable", data["error_message"])

        with mock.patch.object(cli, "JevClient", side_effect=JevApiError("Clé TypeSafe absente ... --mock")):
            code, out, _ = self.run_cli("--json", "--standalone", "--workspace", self.workspace, "x")
        self.assertEqual((code, json.loads(out)["exit_code"]), (4, 4))

    def test_incomplete_workflow_json(self):
        with mock.patch.object(JevClient, "_mock_validate", return_value=False):
            code, out, _ = self.mock_run("--json", "--max-retries", "1")
        data = json.loads(out)
        self.assertEqual((code, data["exit_code"], data["is_success"]), (2, 2, False))
        self.assertIn("Circuit breaker", data["error_message"])


class TestLogFileAndColor(UxTestCase):
    def test_log_file_receives_the_detailed_logs_and_is_closed_afterwards(self):
        log = self.tmp / "workflow.log"
        code, _, _ = self.mock_run("--log-file", str(log), "-v")
        self.assertEqual(code, 0)
        text = log.read_text(encoding="utf-8")
        self.assertIn("Démarrage du workflow Standalone", text)
        self.assertIn("[INFO]", text)
        log.unlink()   # impossible sous Windows si le handler était resté ouvert

    def test_no_color_disables_ansi_and_animations(self):
        class Tty(io.StringIO):
            def isatty(self):
                return True

        with mock.patch.object(terminal, "_enable_windows_vt", return_value=True), \
                mock.patch.dict(terminal.os.environ, {}, clear=False):
            terminal.os.environ.pop("NO_COLOR", None)
            with contextlib.redirect_stdout(Tty()):
                self.assertTrue(terminal.is_ansi_supported())
                self.assertIn("\x1b[32m", terminal.format_colored_diff("+ajout"))
                terminal.set_color_enabled(False)
                self.assertFalse(terminal.is_ansi_supported())
                self.assertEqual(terminal.format_colored_diff("+ajout"), "+ajout")

    def test_no_color_flag_and_environment_variable(self):
        self.mock_run("--no-color")
        self.assertFalse(terminal._color_enabled)

        terminal.set_color_enabled(True)

        class Tty(io.StringIO):
            def isatty(self):
                return True

        with mock.patch.object(terminal, "_enable_windows_vt", return_value=True), \
                mock.patch.dict(terminal.os.environ, {"NO_COLOR": "1"}):
            with contextlib.redirect_stdout(Tty()):
                self.assertFalse(terminal.is_ansi_supported())


class TestReportsAreAlwaysWritten(UxTestCase):
    def read(self, name):
        return (Path(self.workspace) / name).read_text(encoding="utf-8")

    def test_standalone_failure_writes_audit_json_and_rejected_solution(self):
        with mock.patch.object(JevClient, "_mock_validate", return_value=False):
            code, _, _ = self.mock_run("--max-retries", "1")
        self.assertEqual(code, 2)
        audit = self.read("WORKFLOW_AUDIT.md")
        self.assertIn("**Statut :** ÉCHEC", audit)
        self.assertIn("Circuit breaker", audit)
        self.assertIn("SIMULATION", audit)
        self.assertIn("## Décisions Jev", audit)
        data = json.loads(self.read("report.json"))
        self.assertFalse(data["is_success"])
        self.assertTrue((Path(self.workspace) / "rejected_solution.py").is_file())
        self.assertFalse((Path(self.workspace) / "generated_solution.py").exists())

    def test_success_writes_the_solution_audit_and_json(self):
        self.mock_run()
        self.assertTrue((Path(self.workspace) / "generated_solution.py").is_file())
        self.assertIn("**Statut :** SUCCÈS", self.read("WORKFLOW_AUDIT.md"))
        self.assertTrue(json.loads(self.read("report.json"))["is_success"])
        self.assertIn("PROPOSITION DE COMMIT", self.read("GENERATED_DOC.md"))

    def test_exception_mid_run_still_leaves_a_report(self):
        with mock.patch.object(JevClient, "validate", side_effect=JevApiError("TypeSafe Jev indisponible (HTTP 503)")):
            code, _, _ = self.mock_run()
        self.assertEqual(code, cli.EXIT_JEV)
        self.assertIn("ÉCHEC — JevApiError", self.read("WORKFLOW_AUDIT.md"))
        data = json.loads(self.read("report.json"))
        self.assertFalse(data["is_success"])
        self.assertIn("503", data["error_message"])

    def test_keyboard_interrupt_still_leaves_a_report(self):
        with mock.patch.object(JevClient, "validate", side_effect=KeyboardInterrupt):
            code, _, _ = self.mock_run()
        self.assertEqual(code, cli.EXIT_INTERRUPTED)
        self.assertIn("KeyboardInterrupt", self.read("report.json"))

    def test_in_repo_failure_keeps_the_last_attempt_and_the_repository_clean(self):
        repo = make_repo()
        self.addCleanup(repo.cleanup)

        class RejectingJev(JevClient):
            def __init__(self):
                super().__init__(mock_mode=True)

            def validate(self, context, criteria, threshold=None):
                return False

        orchestrator = MultiAgentOrchestrator(
            claude_client=ClaudeCliClient(mock_mode=True), jev_client=RejectingJev(), project_dir=str(repo.path),
            workspace_dir=self.workspace, run_tests=False, use_branch=True, max_retries=1,
        )
        report = orchestrator.run(PROMPT)

        self.assertFalse(report.is_success)
        self.assertIn("TabX.tsx", self.read("FAILED_ATTEMPT.diff"))
        self.assertFalse((Path(self.workspace) / "LATEST_PATCH.diff").exists())
        self.assertEqual(repo.status(), "")
        self.assertEqual(repo.current_branch(), "main")


class TestWorkspaceInsideTheRepository(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        self.addCleanup(self.repo.cleanup)

    def run_in_repo(self, workspace):
        return MultiAgentOrchestrator(
            claude_client=ClaudeCliClient(mock_mode=True), jev_client=JevClient(mock_mode=True),
            project_dir=str(self.repo.path), workspace_dir=str(workspace), run_tests=False, use_branch=True,
        ).run(PROMPT)

    def exclude_lines(self):
        path = self.repo.path / ".git" / "info" / "exclude"
        return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []

    def test_reports_inside_the_repo_are_excluded_locally_and_never_pollute_git_status(self):
        report = self.run_in_repo(self.repo.path / "output")
        self.assertTrue(report.is_success)
        self.assertTrue((self.repo.path / "output" / "WORKFLOW_AUDIT.md").is_file())
        self.assertEqual(self.repo.status(), "")
        self.assertIn("/output/", self.exclude_lines())

    def test_the_exclude_line_is_added_only_once(self):
        self.run_in_repo(self.repo.path / "output")
        self.run_in_repo(self.repo.path / "output")
        self.assertEqual(self.exclude_lines().count("/output/"), 1)

    def test_a_workspace_already_ignored_by_gitignore_is_left_alone(self):
        (self.repo.path / ".gitignore").write_text("rapports/\n", encoding="utf-8")
        self.repo.git("add", ".gitignore")
        self.repo.git("-c", "user.name=T", "-c", "user.email=t@e.x", "commit", "-m", "ignore")
        self.run_in_repo(self.repo.path / "rapports")
        self.assertNotIn("/rapports/", self.exclude_lines())

    def test_a_workspace_outside_the_repo_touches_nothing(self):
        with tempfile.TemporaryDirectory() as outside:
            self.run_in_repo(Path(outside) / "ws")
            self.assertTrue((Path(outside) / "ws" / "report.json").is_file())
        self.assertEqual([l for l in self.exclude_lines() if l.startswith("/")], [])


class TestReportSerialization(unittest.TestCase):
    def make_report(self):
        report = WorkflowExecutionReport(prompt_simple="p", workflow_type=WorkflowType.MOYENNE, dev_specialty=DevSpecialty.PYTHON)
        report.git_diff = "+secret_in_diff"
        report.code_produit = "code"
        report.history.append(__import__("workflow_claude.models", fromlist=["StepRecord"]).StepRecord(
            "S1", "sonnet", "PROMPT_CONTENT", "OUTPUT_CONTENT", 1.23456, {"cost_usd": 0.01}))
        return report

    def test_default_dict_is_json_serializable_without_texts(self):
        data = self.make_report().to_dict()
        text = json.dumps(data)
        self.assertEqual(data["workflow_type"], "Tâche Moyenne")      # énumération convertie
        self.assertEqual(data["dev_specialty"], "Dev Python")
        self.assertNotIn("secret_in_diff", text)
        self.assertNotIn("PROMPT_CONTENT", text)
        self.assertNotIn("OUTPUT_CONTENT", text)
        self.assertEqual(data["history"][0], {"step_name": "S1", "model": "sonnet", "duration_seconds": 1.235,
                                              "metadata": {"cost_usd": 0.01}})

    def test_texts_can_be_included_explicitly(self):
        data = self.make_report().to_dict(include_texts=True)
        self.assertEqual(data["git_diff"], "+secret_in_diff")
        self.assertEqual(data["history"][0]["prompt_sent"], "PROMPT_CONTENT")
        json.dumps(data)


if __name__ == "__main__":
    unittest.main()
    sys.exit(0)

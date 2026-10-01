"""
Tests du point d'entrée CLI : codes de sortie, refus sans clé Jev, bandeau de simulation, options.
Toujours en --standalone ou sur un dépôt temporaire : jamais sur le vrai dépôt, jamais de réseau.
"""

import contextlib
import io
import tempfile
import unittest
from unittest import mock

from gitrepo import make_repo

from workflow_claude import cli
from workflow_claude.clients.claude_cli import ClaudeAuthError, ClaudeCliClient, ClaudeQuotaError
from workflow_claude.clients.jev_client import JevApiError, JevAuthError, JevClient, JevConfigError


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = self._tmp.name

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def mock_run(self, *extra, prompt="Crée un module"):
        return self.run_cli("--mock", "--standalone", "--workspace", self.workspace, *extra, prompt)


class TestExitCodes(CliTestCase):
    def test_success_returns_0_and_shows_the_simulation_banner(self):
        code, out, _ = self.mock_run()
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(code, 0)
        self.assertIn("MODE SIMULATION", out)
        self.assertIn("NE SONT PAS FIABLES", out)
        self.assertIn("WORKFLOW TERMINÉ AVEC SUCCÈS", out)
        self.assertIn("Jev              : SIMULATION", out)

    def test_incomplete_workflow_returns_2(self):
        """Avant : « WORKFLOW INCOMPLET » s'affichait mais le code de sortie restait 0."""
        with mock.patch.object(JevClient, "_mock_validate", return_value=False):
            code, out, _ = self.mock_run("--max-retries", "1")
        self.assertEqual(code, cli.EXIT_INCOMPLETE)
        self.assertEqual(code, 2)
        self.assertIn("WORKFLOW INCOMPLET", out)
        self.assertIn("Circuit breaker", out)

    def test_claude_quota_error_returns_3(self):
        with mock.patch.object(ClaudeCliClient, "run", side_effect=ClaudeQuotaError("limite atteinte, resets 5pm")):
            code, _, err = self.mock_run()
        self.assertEqual(code, cli.EXIT_CLAUDE)
        self.assertIn("resets 5pm", err)
        self.assertIn("remis en état", err)

    def test_claude_auth_error_returns_3(self):
        with mock.patch.object(ClaudeCliClient, "run", side_effect=ClaudeAuthError("non connecté")):
            code, _, _ = self.mock_run()
        self.assertEqual(code, 3)

    def test_jev_error_returns_4_and_says_nothing_was_validated(self):
        with mock.patch.object(JevClient, "validate", side_effect=JevApiError("TypeSafe Jev indisponible (HTTP 503)")):
            code, _, err = self.mock_run()
        self.assertEqual(code, cli.EXIT_JEV)
        self.assertIn("TypeSafe Jev", err)
        self.assertIn("aucune validation par défaut", err)

    def test_jev_auth_error_returns_4(self):
        with mock.patch.object(JevClient, "classify", side_effect=JevAuthError("Clé TypeSafe refusée")):
            code, _, _ = self.mock_run()
        self.assertEqual(code, 4)

    def test_keyboard_interrupt_returns_130(self):
        with mock.patch.object(cli.MultiAgentOrchestrator, "run", side_effect=KeyboardInterrupt):
            code, _, err = self.mock_run()
        self.assertEqual(code, cli.EXIT_INTERRUPTED)
        self.assertEqual(code, 130)
        self.assertIn("remis dans son état d'origine", err)

    def test_unexpected_error_returns_1(self):
        with mock.patch.object(cli.MultiAgentOrchestrator, "run", side_effect=RuntimeError("bug inattendu")):
            code, _, err = self.mock_run()
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("bug inattendu", err)

    def test_precondition_error_returns_1_with_a_clear_message(self):
        empty = make_repo(commit=False)
        self.addCleanup(empty.cleanup)
        code, _, err = self.run_cli("--mock", "--project-dir", str(empty.path), "--workspace", self.workspace, "x")
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("aucun commit", err)


class TestMissingJevKey(CliTestCase):
    def test_refuses_to_start_without_key_and_without_mock(self):
        with mock.patch.object(cli, "JevClient", side_effect=JevConfigError("Clé TypeSafe absente ... --mock ...")) as jev:
            with mock.patch.object(cli, "MultiAgentOrchestrator") as orchestrator:
                code, _, err = self.run_cli("--standalone", "--workspace", self.workspace, "Crée un module")
        self.assertEqual(code, cli.EXIT_JEV)
        self.assertIn("--mock", err)
        jev.assert_called_once_with(mock_mode=False)
        orchestrator.assert_not_called()  # rien n'est lancé : pas de dépense de quota Claude

    def test_mock_flag_bypasses_the_key_requirement(self):
        with mock.patch.object(cli, "JevClient", wraps=JevClient) as jev:
            code, _, _ = self.mock_run()
        self.assertEqual(code, 0)
        jev.assert_called_once_with(mock_mode=True)


class TestOptions(CliTestCase):
    def test_doctor_runs_without_a_prompt_and_returns_its_code(self):
        with mock.patch.object(cli, "run_doctor", return_value=1) as doctor:
            code, _, _ = self.run_cli("--doctor")
        self.assertEqual(code, 1)
        self.assertEqual(doctor.call_args.kwargs["mock"], False)

        with mock.patch.object(cli, "run_doctor", return_value=0) as doctor:
            code, _, _ = self.run_cli("--doctor", "--mock")
        self.assertEqual(code, 0)
        self.assertEqual(doctor.call_args.kwargs["mock"], True)

    def test_jev_send_option_reaches_the_orchestrator(self):
        with mock.patch.object(cli, "MultiAgentOrchestrator") as orchestrator:
            orchestrator.return_value.run.return_value = mock.Mock(
                is_success=True, is_in_repo=False, workflow_type=None, dev_specialty=None, iterations_count=0,
                history=[], jev_mode="mock", decisions=[], code_produit="", doc_et_commit="", error_message=None,
            )
            self.mock_run("--jev-send", "review-only")
        self.assertEqual(orchestrator.call_args.kwargs["jev_send"], "review-only")

    def test_doc_edit_flag_reaches_the_orchestrator_and_defaults_to_enabled(self):
        stub = dict(
            is_success=True, is_in_repo=False, workflow_type=None, dev_specialty=None, iterations_count=0,
            history=[], jev_mode="mock", decisions=[], code_produit="", doc_et_commit="", error_message=None,
        )
        for argv, expected in (((), True), (("--no-doc-edit",), False), (("--doc-edit",), True)):
            with self.subTest(argv=argv):
                with mock.patch.object(cli, "MultiAgentOrchestrator") as orchestrator:
                    orchestrator.return_value.run.return_value = mock.Mock(**stub)
                    _, out, _ = self.mock_run(*argv)
                self.assertEqual(orchestrator.call_args.kwargs["doc_edit"], expected)
                self.assertIn("Doc du projet", out)

    def test_invalid_jev_send_value_is_rejected_by_argparse(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.main(["--mock", "--standalone", "--jev-send", "everything", "x"])
        self.assertEqual(ctx.exception.code, 2)

    def test_decisions_are_listed_in_the_summary(self):
        _, out, _ = self.mock_run()
        self.assertIn("Décisions Jev", out)
        self.assertIn("ROUTAGE_COMPLEXITE", out)
        self.assertIn("VALIDATION_QUALITE_CYCLE_1", out)


if __name__ == "__main__":
    unittest.main()

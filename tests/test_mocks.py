import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from gitrepo import make_repo

from workflow_claude import cli
from workflow_claude.clients.claude_cli import ClaudeCliClient
from workflow_claude.clients.mocks import MOCK_MARKER_FILE, MockClaude, infer_role
from workflow_claude.roles import Role


class TestRoleKeyedMock(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        self.addCleanup(self.repo.cleanup)
        self.mock = MockClaude(edit_files=True)

    def test_each_role_gets_its_own_kind_of_answer(self):
        self.assertIn("Spécification", self.mock.respond("x", "sonnet", role=Role.SPEC, tools=""))
        self.assertIn("```python", self.mock.respond("x", "sonnet", role=Role.DEV))
        self.assertIn("ANALYSE QUALITÉ", self.mock.respond("x", "sonnet", role=Role.QUALITY))
        self.assertIn("AUDIT SÉCURITÉ", self.mock.respond("x", "sonnet", role=Role.SECURITY))
        self.assertIn("Corriger", self.mock.respond("x", "haiku", role=Role.FEEDBACK))
        self.assertIn("PROPOSITION DE COMMIT GIT", self.mock.respond("x", "haiku", role=Role.DOC))

    def test_a_review_whose_diff_mentions_in_situ_never_edits_files(self):
        """Régression : la revue d'un diff contenant « in-situ edit » déclenchait la branche du développeur."""
        before = self.repo.read("src/components/TabX.tsx")
        diff = "+  .sort((a, b) => b.date - a.date) /* in-situ edit */\n"
        prompt = "Tu es un Senior Code Reviewer. Analyse uniquement le git diff ci-dessous :\n" + diff
        self.mock.respond(prompt, "sonnet", role=Role.QUALITY, cwd=str(self.repo.path))
        self.mock.respond(prompt, "sonnet", role=Role.SECURITY, cwd=str(self.repo.path))
        self.assertEqual(self.repo.read("src/components/TabX.tsx"), before)
        self.assertEqual(self.repo.status(), "")

    def test_in_place_dev_edits_the_target_file(self):
        self.mock.respond("Applique directement les modifications dans src/components/TabX.tsx", "sonnet",
                          role=Role.DEV, cwd=str(self.repo.path), permission_mode="acceptEdits")
        self.assertIn("in-situ edit", self.repo.read("src/components/TabX.tsx"))

    def test_standalone_dev_returns_code_and_touches_nothing(self):
        out = self.mock.respond("Implémente la solution", "sonnet", role=Role.DEV, cwd=str(self.repo.path))
        self.assertIn("process_task", out)
        self.assertEqual(self.repo.status(), "")

    def test_marker_mode_never_touches_project_files(self):
        marker_mock = MockClaude(edit_files=False)
        before = self.repo.read("src/components/TabX.tsx")
        marker_mock.respond("Applique directement les modifications", "sonnet", role=Role.DEV,
                            cwd=str(self.repo.path), permission_mode="acceptEdits")
        marker_mock.respond("Applique directement les modifications", "sonnet", role=Role.DEV,
                            cwd=str(self.repo.path), permission_mode="acceptEdits")
        self.assertEqual(self.repo.read("src/components/TabX.tsx"), before)
        self.assertEqual(self.repo.status(), f"?? {MOCK_MARKER_FILE}")
        self.assertEqual(self.repo.read(MOCK_MARKER_FILE).count("- modification simulée"), 2)

    def test_doc_edit_mock(self):
        self.mock.respond("x", "haiku", role=Role.DOC_EDIT, cwd=str(self.repo.path))
        self.assertIn("Documentation mise à jour", self.repo.read("CHANGELOG.md"))
        other = make_repo()
        self.addCleanup(other.cleanup)
        MockClaude(edit_files=False).respond("x", "haiku", role=Role.DOC_EDIT, cwd=str(other.path))
        self.assertEqual(other.status(), "")

    def test_role_inference_when_no_role_is_given(self):
        self.assertIs(infer_role("Explore le codebase ..."), Role.SPEC)
        self.assertIs(infer_role("Tu es un Senior Code Reviewer"), Role.QUALITY)
        self.assertIs(infer_role("Tu es un Expert en Cyber-Sécurité"), Role.SECURITY)
        self.assertIs(infer_role("Tu es un Technical Writer & Git Master"), Role.DOC)
        self.assertIsNone(infer_role("bonjour"))
        self.assertIn("Simulation", self.mock.respond("bonjour", "sonnet"))

    def test_client_delegates_to_the_mock_and_clears_last_result(self):
        client = ClaudeCliClient(mock_mode=True)
        client.last_result = object()
        self.assertIn("ANALYSE QUALITÉ", client.run("x", "sonnet", role=Role.QUALITY))
        self.assertIsNone(client.last_result)


class TestCliMockNeverTouchesRealCode(unittest.TestCase):
    def test_mock_run_on_a_real_repository_only_writes_the_marker_file(self):
        repo = make_repo()
        self.addCleanup(repo.cleanup)
        initial = repo.git("rev-parse", "HEAD")
        originals = {p: repo.read(p) for p in ("src/components/TabX.tsx", "src/components/TabY.tsx", "README.md")}

        with tempfile.TemporaryDirectory() as workspace:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["--mock", "--yes", "--project-dir", str(repo.path), "--workspace", workspace, "Modifie le tri"])

        self.assertEqual(code, 0, err.getvalue())
        changed = repo.git("diff", "--name-only", f"{initial}..HEAD").splitlines()
        self.assertEqual(changed, [MOCK_MARKER_FILE])
        for path, content in originals.items():
            self.assertEqual(repo.read(path), content, path)
        self.assertTrue(Path(repo.path, MOCK_MARKER_FILE).is_file())


if __name__ == "__main__":
    unittest.main()

"""
Agent de documentation : met à jour la doc du projet (dans le même commit) sous garde-fou. Tout ce qui n'est pas
de la documentation est annulé, le code déjà validé n'est jamais modifié, et une panne de l'agent ne remet pas en
cause le travail validé.
"""

import tempfile
import unittest
from pathlib import Path

from gitrepo import make_repo

from workflow_claude import doc_guard
from workflow_claude.clients.claude_cli import ClaudeCliClient, ClaudeCliError, ClaudeQuotaError
from workflow_claude.clients.git_client import GitClient
from workflow_claude.clients.jev_client import JevClient
from workflow_claude.doc_guard import is_doc_path
from workflow_claude.models import WorkflowType
from workflow_claude.orchestrator import MultiAgentOrchestrator
from workflow_claude.roles import Role

PROMPT = "Modifie la fonction de tri dans l'onglet x"


class DocAgentClaude(ClaudeCliClient):
    """Claude simulé dont l'agent de documentation exécute l'action donnée dans le dépôt."""

    def __init__(self, repo_path, action):
        super().__init__(mock_mode=True)
        self.repo_path = Path(repo_path)
        self.action = action
        self.doc_calls = 0

    def run(self, prompt, model, **kwargs):
        if kwargs.get("role") is Role.DOC_EDIT:
            self.doc_calls += 1
            self.action(self.repo_path)
            return "documentation traitée"
        return super().run(prompt, model, **kwargs)


class ModerateJev(JevClient):
    def __init__(self):
        super().__init__(mock_mode=True)

    def classify(self, context, choices, question_label="", **kwargs):
        return WorkflowType.SIMPLE.value if WorkflowType.SIMPLE.value in choices else choices[0]


def write(root, rel, content):
    target = Path(root) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")


class TestIsDocPath(unittest.TestCase):
    def test_documentation_paths(self):
        for path in ("README.md", "CHANGELOG.md", "CHANGELOG", "docs/guide.md", "docs/deep/dir/a.rst", "notes.adoc",
                     "a/b/README", "History", "pkg/CHANGES", "guide.MDX", "x/y/Readme.markdown"):
            self.assertTrue(is_doc_path(path), path)

    def test_code_config_tests_and_legal_files_are_not_documentation(self):
        for path in ("src/app.py", "package.json", "pyproject.toml", "tests/test_x.py", "Makefile", "LICENSE",
                     "docs/conf.py", "docs/build.sh", ".github/workflows/ci.yml", "requirements.txt", "readme.py",
                     ".git/config.md", "src/notes.txt"):
            self.assertFalse(is_doc_path(path), path)

    def test_windows_separators(self):
        self.assertTrue(is_doc_path("docs\\guide.md"))
        self.assertFalse(is_doc_path("src\\app.py"))


class TestDocGuard(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo({"docs/guide.md": "# Guide\n", "src/app.py": "def app():\n    return 1\n"})
        self.addCleanup(self.repo.cleanup)
        self.git = GitClient(str(self.repo.path))

    def test_documentation_edits_are_kept(self):
        snap = doc_guard.snapshot(self.git)
        write(self.repo.path, "README.md", "# Test Project\nNouveau paragraphe\n")
        write(self.repo.path, "docs/guide.md", "# Guide\nMis à jour\n")
        write(self.repo.path, "CHANGELOG.md", "# Changelog\n")
        result = doc_guard.enforce(self.git, snap)
        self.assertEqual(sorted(result.kept), ["CHANGELOG.md", "README.md", "docs/guide.md"])
        self.assertEqual(result.reverted, [])

    def test_new_code_and_config_files_are_deleted(self):
        snap = doc_guard.snapshot(self.git)
        write(self.repo.path, "src/evil.py", "import os\n")
        write(self.repo.path, "package.json", "{}\n")
        write(self.repo.path, "README.md", "# Test Project\nok\n")
        result = doc_guard.enforce(self.git, snap)
        self.assertEqual(result.kept, ["README.md"])
        self.assertEqual(sorted(result.reverted), ["package.json", "src/evil.py"])
        self.assertFalse((self.repo.path / "src" / "evil.py").exists())
        self.assertFalse((self.repo.path / "package.json").exists())

    def test_tracked_code_changed_by_the_agent_is_restored_from_head(self):
        snap = doc_guard.snapshot(self.git)
        write(self.repo.path, "src/app.py", "def app():\n    return 'piraté'\n")
        result = doc_guard.enforce(self.git, snap)
        self.assertEqual(result.reverted, ["src/app.py"])
        self.assertEqual(self.repo.read("src/app.py"), "def app():\n    return 1\n")
        self.assertEqual(self.repo.status(), "")

    def test_deletions_are_reverted_even_for_documentation(self):
        snap = doc_guard.snapshot(self.git)
        (self.repo.path / "docs" / "guide.md").unlink()
        (self.repo.path / "src" / "app.py").unlink()
        result = doc_guard.enforce(self.git, snap)
        self.assertEqual(sorted(result.reverted), ["docs/guide.md", "src/app.py"])
        self.assertTrue((self.repo.path / "docs" / "guide.md").exists())
        self.assertTrue((self.repo.path / "src" / "app.py").exists())

    def test_validated_code_already_modified_by_the_developer_is_protected(self):
        write(self.repo.path, "src/app.py", "def app():\n    return 2  # validé\n")   # travail du développeur
        write(self.repo.path, "src/new_feature.py", "def feature():\n    return 1\n")
        snap = doc_guard.snapshot(self.git)

        write(self.repo.path, "src/app.py", "def app():\n    return 'modifié par l agent doc'\n")
        (self.repo.path / "src" / "new_feature.py").unlink()
        write(self.repo.path, "README.md", "# Test Project\ndoc\n")

        result = doc_guard.enforce(self.git, snap)
        self.assertEqual(result.kept, ["README.md"])
        self.assertEqual(sorted(result.reverted), ["src/app.py", "src/new_feature.py"])
        self.assertEqual(self.repo.read("src/app.py"), "def app():\n    return 2  # validé\n")
        self.assertEqual(self.repo.read("src/new_feature.py"), "def feature():\n    return 1\n")

    def test_developer_change_reset_to_head_by_the_agent_is_restored(self):
        write(self.repo.path, "src/app.py", "def app():\n    return 2\n")
        snap = doc_guard.snapshot(self.git)
        self.repo.git("checkout", "HEAD", "--", "src/app.py")   # l'agent « annule » le travail du développeur
        result = doc_guard.enforce(self.git, snap)
        self.assertEqual(result.reverted, ["src/app.py"])
        self.assertEqual(self.repo.read("src/app.py"), "def app():\n    return 2\n")

    def test_doc_file_already_changed_by_the_developer_can_be_updated_further(self):
        write(self.repo.path, "README.md", "# Test Project\nligne du dev\n")
        snap = doc_guard.snapshot(self.git)
        write(self.repo.path, "README.md", "# Test Project\nligne du dev\nligne de l'agent doc\n")
        result = doc_guard.enforce(self.git, snap)
        self.assertEqual(result.kept, ["README.md"])

    def test_nothing_changed_is_a_noop(self):
        snap = doc_guard.snapshot(self.git)
        result = doc_guard.enforce(self.git, snap)
        self.assertEqual((result.kept, result.reverted), ([], []))


class TestDocEditInTheWorkflow(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        self.addCleanup(self.repo.cleanup)
        self.initial = self.repo.git("rev-parse", "HEAD")

    def orchestrator(self, claude, **kwargs):
        options = dict(claude_client=claude, jev_client=ModerateJev(), project_dir=str(self.repo.path),
                       run_tests=False, use_branch=True)
        options.update(kwargs)
        return MultiAgentOrchestrator(**options)

    def committed_files(self, report):
        return self.repo.git("diff", "--name-only", f"{self.initial}..{report.branch_name}").splitlines()

    def test_default_agent_updates_the_changelog_in_the_same_commit_as_the_code(self):
        report = self.orchestrator(ClaudeCliClient(mock_mode=True)).run(PROMPT)

        self.assertTrue(report.is_success)
        self.assertEqual(report.doc_files_kept, ["CHANGELOG.md"])
        self.assertEqual(report.doc_files_reverted, [])
        self.assertEqual(sorted(self.committed_files(report)), ["CHANGELOG.md", "src/components/TabX.tsx"])
        self.assertEqual(self.repo.git("rev-list", "--count", f"{self.initial}..{report.branch_name}"), "1")
        self.assertIn("DOC_EDIT", [s.step_name for s in report.history])
        self.assertIn("Documentation mise à jour", self.repo.git("show", f"{report.branch_name}:CHANGELOG.md"))

    def test_doc_diff_is_separate_from_the_code_patch(self):
        report = self.orchestrator(ClaudeCliClient(mock_mode=True)).run(PROMPT)
        self.assertIn("CHANGELOG.md", report.doc_diff)
        self.assertNotIn("TabX.tsx", report.doc_diff)
        self.assertIn("TabX.tsx", report.git_diff)
        self.assertNotIn("CHANGELOG.md", report.git_diff)
        self.assertIn("CHANGELOG.md", report.modified_files)

    def test_code_touched_by_the_doc_agent_is_reverted_and_never_committed(self):
        def malicious(root):
            write(root, "README.md", "# Test Project\nmise à jour doc\n")
            write(root, "src/components/Backdoor.tsx", "export const backdoor = 1;\n")
            write(root, "src/components/TabY.tsx", "export const TabY = () => <div>hacked</div>;\n")
            write(root, "package.json", '{"name": "hijacked"}\n')

        report = self.orchestrator(DocAgentClaude(self.repo.path, malicious)).run(PROMPT)

        self.assertTrue(report.is_success)
        self.assertEqual(report.doc_files_kept, ["README.md"])
        self.assertEqual(sorted(report.doc_files_reverted),
                         ["package.json", "src/components/Backdoor.tsx", "src/components/TabY.tsx"])
        committed = self.committed_files(report)
        self.assertEqual(sorted(committed), ["README.md", "src/components/TabX.tsx"])
        self.assertEqual(self.repo.git("show", f"{report.branch_name}:src/components/TabY.tsx"),
                         "export const TabY = () => <div>Tab Y</div>;")

    def test_the_developers_validated_code_is_untouched_by_the_doc_agent(self):
        snapshots = {}

        def rewrite_everything(root):
            snapshots["before"] = (Path(root) / "src/components/TabX.tsx").read_text(encoding="utf-8")
            write(root, "src/components/TabX.tsx", "export const TabX = () => null;\n")

        report = self.orchestrator(DocAgentClaude(self.repo.path, rewrite_everything)).run(PROMPT)
        self.assertTrue(report.is_success)
        committed_tabx = self.repo.git("show", f"{report.branch_name}:src/components/TabX.tsx")
        self.assertIn("in-situ edit", committed_tabx)   # l'édition validée du développeur est conservée
        self.assertIn("src/components/TabX.tsx", report.doc_files_reverted)

    def test_no_doc_edit_option_skips_the_step(self):
        claude = DocAgentClaude(self.repo.path, lambda root: write(root, "README.md", "x\n"))
        report = self.orchestrator(claude, doc_edit=False).run(PROMPT)
        self.assertTrue(report.is_success)
        self.assertEqual(claude.doc_calls, 0)
        self.assertNotIn("DOC_EDIT", [s.step_name for s in report.history])
        self.assertEqual(self.committed_files(report), ["src/components/TabX.tsx"])

    def test_nothing_to_document_when_the_developer_changed_nothing(self):
        class NoOpDev(ClaudeCliClient):
            def __init__(self):
                super().__init__(mock_mode=True)
                self.doc_calls = 0

            def run(self, prompt, model, **kwargs):
                if kwargs.get("role") is Role.DOC_EDIT:
                    self.doc_calls += 1
                if kwargs.get("role") is Role.DEV:
                    return "Rien à modifier."
                return super().run(prompt, model, **kwargs)

        claude = NoOpDev()
        report = self.orchestrator(claude).run(PROMPT)
        self.assertTrue(report.is_success)
        self.assertEqual(claude.doc_calls, 0)
        self.assertIsNone(report.commit_hash)

    def test_doc_agent_failure_never_discards_the_validated_code(self):
        def fail_after_writing(root):
            write(root, "README.md", "# Test Project\nécrit avant l'échec\n")
            raise ClaudeCliError("timeout de l'agent doc")

        with self.assertLogs("orchestrator", level="WARNING") as logs:
            report = self.orchestrator(DocAgentClaude(self.repo.path, fail_after_writing)).run(PROMPT)

        self.assertTrue(report.is_success)
        self.assertIn("abandonnée", "\n".join(logs.output))
        self.assertIn("src/components/TabX.tsx", self.committed_files(report))   # le code validé est bien commité
        self.assertEqual(report.doc_files_kept, ["README.md"])                    # ce qui a été écrit avant l'échec est gardé

    def test_quota_error_in_the_doc_agent_is_also_non_fatal(self):
        def quota(root):
            raise ClaudeQuotaError("limite atteinte")

        report = self.orchestrator(DocAgentClaude(self.repo.path, quota)).run(PROMPT)
        self.assertTrue(report.is_success)
        self.assertEqual(self.committed_files(report), ["src/components/TabX.tsx"])

    def test_keyboard_interrupt_still_propagates_and_restores_everything(self):
        def interrupt(root):
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            self.orchestrator(DocAgentClaude(self.repo.path, interrupt)).run(PROMPT)
        self.assertEqual(self.repo.current_branch(), "main")
        self.assertEqual(self.repo.status(), "")
        self.assertEqual([b for b in self.repo.branches() if b.startswith("workflow/")], [])

    def test_doc_changes_are_persisted_separately(self):
        with tempfile.TemporaryDirectory() as workspace:
            report = self.orchestrator(ClaudeCliClient(mock_mode=True), workspace_dir=workspace).run(PROMPT)
            self.assertTrue(report.is_success)
            self.assertIn("CHANGELOG.md", (Path(workspace) / "DOC_CHANGES.diff").read_text(encoding="utf-8"))
            self.assertNotIn("CHANGELOG.md", (Path(workspace) / "LATEST_PATCH.diff").read_text(encoding="utf-8"))
            self.assertIn("CHANGELOG.md", (Path(workspace) / "WORKFLOW_AUDIT.md").read_text(encoding="utf-8"))

    def test_the_doc_agent_runs_with_edit_tools_in_the_project_directory(self):
        seen = []

        class Spy(ClaudeCliClient):
            def run(self, prompt, model, **kwargs):
                if kwargs.get("role") is Role.DOC_EDIT:
                    seen.append((kwargs, prompt))
                return super().run(prompt, model, **kwargs)

        self.orchestrator(Spy(mock_mode=True), allow_bash=True).run(PROMPT)
        options, prompt = seen[0]
        self.assertIn("Edit", options["tools"].split(","))
        self.assertNotIn("Bash", options["tools"].split(","))        # jamais de Bash pour l'agent doc
        self.assertEqual(options["permission_mode"], "acceptEdits")
        self.assertEqual(Path(options["cwd"]).resolve(), self.repo.path)
        self.assertIn("Ne modifie AUCUN fichier de code", prompt)


class TestDocEditAndGitClient(unittest.TestCase):
    def test_status_entries_and_restore(self):
        repo = make_repo()
        self.addCleanup(repo.cleanup)
        git = GitClient(str(repo.path))
        write(repo.path, "README.md", "changé\n")
        write(repo.path, "nouveau.txt", "n\n")
        entries = dict((path, code.strip()) for code, path in git.status_entries())
        self.assertEqual(entries, {"README.md": "M", "nouveau.txt": "??"})
        git.restore_from_head("README.md")
        self.assertEqual(repo.read("README.md"), "# Test Project\n")

    def test_get_diff_can_be_limited_to_paths(self):
        repo = make_repo()
        self.addCleanup(repo.cleanup)
        git = GitClient(str(repo.path))
        write(repo.path, "README.md", "changé\n")
        write(repo.path, "src/components/TabY.tsx", "autre\n")
        diff = git.get_diff(paths=["README.md"])
        self.assertIn("README.md", diff)
        self.assertNotIn("TabY", diff)


if __name__ == "__main__":
    unittest.main()

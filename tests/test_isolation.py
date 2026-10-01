"""
Tests de sécurité des données du mode In-Repo : Stash Guard, branche d'isolation, nettoyage
sur toute sortie (échec, exception, Ctrl-C), commit, fusion et cas limites Git.
"""

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gitrepo import make_repo

from workflow_claude.clients.claude_cli import ClaudeCliClient, ClaudeCliError
from workflow_claude.clients.jev_client import JevClient
from workflow_claude.clients.test_runner import TestResult, TestRunner
from workflow_claude.isolation import WorkflowPreconditionError
from workflow_claude.orchestrator import MultiAgentOrchestrator, WorkflowMaxRetriesExceeded

PROMPT = "Modifie la fonction de tri dans l'onglet x"


class EditThenFailClaude(ClaudeCliClient):
    """Applique réellement l'édition mock du dev, puis échoue (timeout, quota, Ctrl-C...)."""

    def __init__(self, exc):
        super().__init__(mock_mode=True)
        self.exc = exc

    def run(self, prompt, model, **kwargs):
        out = super().run(prompt, model, **kwargs)
        if kwargs.get("permission_mode") == "acceptEdits":
            raise self.exc
        return out


class NoOpDevClaude(ClaudeCliClient):
    """Le dev « répond » sans rien modifier dans le dépôt."""

    def __init__(self):
        super().__init__(mock_mode=True)

    def run(self, prompt, model, **kwargs):
        if kwargs.get("permission_mode") == "acceptEdits":
            return "Rien à modifier."
        return super().run(prompt, model, **kwargs)


class AlwaysRejectJev(JevClient):
    def __init__(self):
        super().__init__(mock_mode=True)

    def validate(self, context, criteria, threshold=0.5):
        return False


class ArtifactCreatingRunner(TestRunner):
    """Simule une suite de tests qui laisse des fichiers non suivis (caches, rapports...)."""

    def run_tests(self, project_dir, custom_cmd=None):
        Path(project_dir, "coverage_report.tmp").write_text("artifact\n", encoding="utf-8")
        return TestResult(passed=True, command="fake-tests", output="OK", duration_seconds=0.0, returncode=0)


class InRepoSafetyTestCase(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        self.addCleanup(self.repo.cleanup)
        self.initial_head = self.repo.git("rev-parse", "HEAD")

    def orchestrator(self, claude=None, jev=None, **kwargs):
        options = dict(
            claude_client=claude or ClaudeCliClient(mock_mode=True),
            jev_client=jev or JevClient(mock_mode=True),
            project_dir=str(self.repo.path),
            run_tests=False,
            use_branch=True,
        )
        options.update(kwargs)
        return MultiAgentOrchestrator(**options)

    def workflow_branches(self):
        return [b for b in self.repo.branches() if b.startswith("workflow/")]

    def assertBackOnOriginalBranch(self):
        self.assertEqual(self.repo.current_branch(), "main")
        self.assertEqual(self.repo.git("rev-parse", "HEAD"), self.initial_head)


class TestSuccessPath(InRepoSafetyTestCase):
    def test_wip_is_restored_on_the_original_branch_not_on_the_work_branch(self):
        self.repo.write("wip.txt", "travail en cours\n")
        self.repo.write("README.md", "# README modifié par l'utilisateur\n")

        report = self.orchestrator().run(PROMPT)

        self.assertTrue(report.is_success)
        self.assertBackOnOriginalBranch()
        self.assertTrue(report.stash_restored)
        self.assertEqual(self.repo.stashes(), [])
        # Le travail en cours est de retour dans l'arbre de travail de l'utilisateur...
        self.assertEqual(self.repo.read("wip.txt"), "travail en cours\n")
        self.assertEqual(self.repo.read("README.md"), "# README modifié par l'utilisateur\n")
        # ... et n'a jamais été mélangé au commit du workflow
        files_in_commit = self.repo.git("ls-tree", "-r", "--name-only", report.branch_name).splitlines()
        self.assertNotIn("wip.txt", files_in_commit)
        self.assertEqual(self.repo.git("show", f"{report.branch_name}:README.md"), "# Test Project")

    def test_merge_decision_true_merges_and_removes_branch(self):
        decisions = []
        report = self.orchestrator(on_merge_decision=lambda rep: decisions.append(rep.commit_hash) or True).run(PROMPT)

        self.assertTrue(report.is_success)
        self.assertEqual(len(decisions), 1)
        self.assertIsNotNone(decisions[0])
        self.assertTrue(report.merged)
        self.assertEqual(self.workflow_branches(), [])
        self.assertEqual(self.repo.current_branch(), "main")
        self.assertIn("in-situ edit", self.repo.read("src/components/TabX.tsx"))
        self.assertEqual(self.repo.status(), "")

    def test_merge_decision_false_keeps_branch_and_origin_untouched(self):
        report = self.orchestrator(on_merge_decision=lambda rep: False).run(PROMPT)

        self.assertTrue(report.is_success)
        self.assertFalse(report.merged)
        self.assertBackOnOriginalBranch()
        self.assertEqual(self.workflow_branches(), [report.branch_name])
        self.assertNotIn("in-situ edit", self.repo.read("src/components/TabX.tsx"))

    def test_merge_then_stash_conflict_keeps_the_stash(self):
        # WIP de l'utilisateur sur la MÊME ligne que celle que le workflow va modifier
        original = self.repo.read("src/components/TabX.tsx")
        self.repo.write("src/components/TabX.tsx", original.replace("localeCompare", "localeCompareUser"))

        report = self.orchestrator(auto_merge=True).run(PROMPT)

        self.assertTrue(report.is_success)
        self.assertTrue(report.merged)
        self.assertFalse(report.stash_restored)          # conflit signalé
        self.assertEqual(len(self.repo.stashes()), 1)    # et travail de l'utilisateur conservé par git
        self.assertEqual(self.repo.current_branch(), "main")

    def test_artifacts_created_by_tests_are_not_committed(self):
        report = self.orchestrator(test_runner=ArtifactCreatingRunner(), run_tests=True).run(PROMPT)

        self.assertTrue(report.is_success)
        files_in_commit = self.repo.git("ls-tree", "-r", "--name-only", report.branch_name).splitlines()
        self.assertNotIn("coverage_report.tmp", files_in_commit)
        self.assertFalse((self.repo.path / "coverage_report.tmp").exists())
        self.assertIn("src/components/TabX.tsx", report.modified_files)

    def test_no_change_means_no_commit_and_no_leftover_branch(self):
        report = self.orchestrator(claude=NoOpDevClaude()).run(PROMPT)

        self.assertTrue(report.is_success)
        self.assertIsNone(report.commit_hash)
        self.assertEqual(self.workflow_branches(), [])
        self.assertBackOnOriginalBranch()
        self.assertEqual(self.repo.status(), "")

    def test_project_in_a_subdirectory_uses_the_repository_root(self):
        self.repo.write("wip_root.txt", "wip à la racine\n")
        orchestrator = self.orchestrator(project_dir=str(self.repo.path / "src"))

        report = orchestrator.run(PROMPT)

        self.assertTrue(report.is_success)
        self.assertBackOnOriginalBranch()
        self.assertEqual(self.repo.read("wip_root.txt"), "wip à la racine\n")  # stash au niveau du dépôt

    def test_detached_head_returns_to_the_same_commit(self):
        self.repo.git("checkout", "--detach", self.initial_head)

        report = self.orchestrator().run(PROMPT)

        self.assertTrue(report.is_success)
        self.assertEqual(self.repo.git("rev-parse", "HEAD"), self.initial_head)
        self.assertEqual(self.repo.git("rev-parse", "--abbrev-ref", "HEAD"), "HEAD")
        self.assertEqual(len(self.workflow_branches()), 1)  # commit conservé sur sa branche


class TestCleanupOnAnyExit(InRepoSafetyTestCase):
    def assertCleanAfterFailure(self):
        self.assertBackOnOriginalBranch()
        self.assertEqual(self.workflow_branches(), [])
        self.assertEqual(self.repo.stashes(), [])
        self.assertEqual(self.repo.read("wip.txt"), "travail en cours\n")
        self.assertEqual(self.repo.read("src/components/TabX.tsx"), self.original_tabx)
        self.assertEqual(self.repo.status(), "?? wip.txt")

    def setUp(self):
        super().setUp()
        self.original_tabx = self.repo.read("src/components/TabX.tsx")
        self.repo.write("wip.txt", "travail en cours\n")

    def test_claude_error_during_dev_restores_everything(self):
        orchestrator = self.orchestrator(claude=EditThenFailClaude(ClaudeCliError("timeout simulé")))
        with self.assertRaises(ClaudeCliError):
            orchestrator.run(PROMPT)
        self.assertCleanAfterFailure()

    def test_keyboard_interrupt_restores_everything(self):
        orchestrator = self.orchestrator(claude=EditThenFailClaude(KeyboardInterrupt()))
        with self.assertRaises(KeyboardInterrupt):
            orchestrator.run(PROMPT)
        self.assertCleanAfterFailure()

    def test_circuit_breaker_restores_everything(self):
        orchestrator = self.orchestrator(jev=AlwaysRejectJev(), max_retries=2)
        report = orchestrator.run(PROMPT)

        self.assertFalse(report.is_success)
        self.assertIn("Circuit breaker", report.error_message)
        self.assertEqual(report.iterations_count, 2)
        self.assertCleanAfterFailure()

    def test_circuit_breaker_raises_when_requested_and_still_cleans_up(self):
        orchestrator = self.orchestrator(jev=AlwaysRejectJev(), max_retries=1)
        with self.assertRaises(WorkflowMaxRetriesExceeded):
            orchestrator.run(PROMPT, raise_on_failure=True)
        self.assertCleanAfterFailure()

    def test_circuit_breaker_without_branch_also_restores_the_working_tree(self):
        orchestrator = self.orchestrator(jev=AlwaysRejectJev(), max_retries=1, use_branch=False)
        report = orchestrator.run(PROMPT)
        self.assertFalse(report.is_success)
        self.assertCleanAfterFailure()

    def test_commit_failure_keeps_the_patch_and_restores_everything(self):
        hook = self.repo.path / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\necho 'refused by hook' >&2\nexit 1\n", encoding="utf-8", newline="\n")
        hook.chmod(hook.stat().st_mode | stat.S_IEXEC)

        with tempfile.TemporaryDirectory() as workspace:
            report = self.orchestrator(workspace_dir=workspace).run(PROMPT)
            patch = Path(workspace) / "LATEST_PATCH.diff"
            self.assertTrue(patch.is_file())
            self.assertIn("TabX.tsx", patch.read_text(encoding="utf-8"))

        self.assertFalse(report.is_success)
        self.assertIn("commit", report.error_message.lower())
        self.assertCleanAfterFailure()


class TestPreconditions(unittest.TestCase):
    def test_empty_repository_is_refused(self):
        repo = make_repo(commit=False)
        self.addCleanup(repo.cleanup)
        orchestrator = MultiAgentOrchestrator(
            claude_client=ClaudeCliClient(mock_mode=True),
            jev_client=JevClient(mock_mode=True),
            project_dir=str(repo.path),
            run_tests=False,
        )
        with self.assertRaises(WorkflowPreconditionError):
            orchestrator.run(PROMPT)

    def test_missing_git_identity_is_refused_before_touching_anything(self):
        repo = make_repo(identity=False)
        self.addCleanup(repo.cleanup)
        repo.write("wip.txt", "à ne pas toucher\n")
        orchestrator = MultiAgentOrchestrator(
            claude_client=ClaudeCliClient(mock_mode=True),
            jev_client=JevClient(mock_mode=True),
            project_dir=str(repo.path),
            run_tests=False,
            use_branch=True,
        )
        isolated_env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        with mock.patch.dict(os.environ, isolated_env):
            for var in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL"):
                os.environ.pop(var, None)
            with self.assertRaises(WorkflowPreconditionError):
                orchestrator.run(PROMPT)

        self.assertEqual(repo.read("wip.txt"), "à ne pas toucher\n")
        self.assertEqual(repo.stashes(), [])
        self.assertEqual(repo.current_branch(), "main")


if __name__ == "__main__":
    unittest.main()

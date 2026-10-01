"""
Cas limites Git de bout en bout : sous-modules, et branche d'origine qui avance PENDANT le workflow
(fusion sans conflit, puis fusion en conflit : jamais de fusion à moitié faite, jamais de travail perdu).
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from gitrepo import git, make_repo

from workflow_claude.clients.claude_cli import ClaudeCliClient
from workflow_claude.clients.jev_client import JevClient
from workflow_claude.isolation import WorkflowPreconditionError
from workflow_claude.orchestrator import MultiAgentOrchestrator
from workflow_claude.roles import Role

PROMPT = "Modifie la fonction de tri dans l'onglet x"
COMMIT = ("-c", "user.name=Other", "-c", "user.email=other@example.com", "commit", "-q", "-m")


class AdvanceMainClaude(ClaudeCliClient):
    """
    Pendant l'appel du développeur (on est donc sur la branche de travail), `main` avance dans un worktree séparé :
    c'est ce qui arrive quand un collègue pousse ou que vous committez ailleurs pendant que l'agent travaille.
    """

    def __init__(self, repo, rel_path, content):
        super().__init__(mock_mode=True)
        self.repo, self.rel_path, self.content = repo, rel_path, content
        self.advanced = False

    def run(self, prompt, model, **kwargs):
        text = super().run(prompt, model, **kwargs)
        if kwargs.get("role") is Role.DEV and not self.advanced:
            self.advanced = True
            with tempfile.TemporaryDirectory() as tmp:
                worktree = Path(tmp) / "wt"
                git(self.repo.path, "worktree", "add", str(worktree), "main")
                target = worktree / self.rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(self.content, encoding="utf-8", newline="\n")
                git(worktree, "add", "-A")
                git(worktree, *COMMIT, "avancée de main pendant le run")
                git(self.repo.path, "worktree", "remove", "--force", str(worktree))
        return text


class TestBranchAdvancesDuringTheRun(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        self.addCleanup(self.repo.cleanup)
        self.repo.write("wip.txt", "travail en cours de l'utilisateur\n")

    def orchestrator(self, claude, **kwargs):
        options = dict(claude_client=claude, jev_client=JevClient(mock_mode=True), project_dir=str(self.repo.path),
                       run_tests=False, use_branch=True, doc_edit=False)
        options.update(kwargs)
        return MultiAgentOrchestrator(**options)

    def workflow_branches(self):
        return [b for b in self.repo.branches() if b.startswith("workflow/")]

    def assertWipIntact(self):
        self.assertEqual(self.repo.read("wip.txt"), "travail en cours de l'utilisateur\n")
        self.assertEqual(self.repo.stashes(), [])

    def test_non_conflicting_divergence_is_merged_with_a_merge_commit(self):
        claude = AdvanceMainClaude(self.repo, "NOTES.md", "ajouté par un collègue\n")
        report = self.orchestrator(claude, auto_merge=True).run(PROMPT)

        self.assertTrue(report.is_success)
        self.assertTrue(report.merged)
        self.assertEqual(self.repo.current_branch(), "main")
        self.assertEqual(self.workflow_branches(), [])
        self.assertIn("in-situ edit", self.repo.read("src/components/TabX.tsx"))      # le travail du workflow
        self.assertEqual(self.repo.read("NOTES.md"), "ajouté par un collègue\n")      # et celui du collègue
        self.assertEqual(self.repo.git("rev-list", "--merges", "--count", "HEAD"), "1")
        self.assertWipIntact()

    def test_conflicting_divergence_leaves_no_half_done_merge_and_keeps_the_branch(self):
        original = self.repo.read("src/components/TabX.tsx")
        conflicting = original.replace("localeCompare", "localeCompare /* version du collègue */")
        claude = AdvanceMainClaude(self.repo, "src/components/TabX.tsx", conflicting)

        with self.assertLogs("git_client", level="ERROR"):
            report = self.orchestrator(claude, auto_merge=True).run(PROMPT)

        self.assertTrue(report.is_success)                  # le travail validé existe bel et bien
        self.assertFalse(report.merged)                     # mais n'a pas pu être fusionné
        self.assertEqual(self.repo.current_branch(), "main")
        self.assertFalse((self.repo.path / ".git" / "MERGE_HEAD").exists())     # aucune fusion en cours
        self.assertNotIn("<<<<<<<", self.repo.read("src/components/TabX.tsx"))   # aucun marqueur de conflit
        self.assertIn("version du collègue", self.repo.read("src/components/TabX.tsx"))   # main intact
        self.assertEqual(self.workflow_branches(), [report.branch_name])         # branche conservée pour inspection
        self.assertIn("in-situ edit", self.repo.git("show", f"{report.branch_name}:src/components/TabX.tsx"))
        self.assertWipIntact()

    def test_user_declining_the_merge_still_returns_to_the_origin_branch(self):
        claude = AdvanceMainClaude(self.repo, "NOTES.md", "collègue\n")
        report = self.orchestrator(claude, on_merge_decision=lambda rep: False).run(PROMPT)

        self.assertTrue(report.is_success)
        self.assertFalse(report.merged)
        self.assertEqual(self.repo.current_branch(), "main")
        self.assertEqual(self.workflow_branches(), [report.branch_name])
        self.assertWipIntact()


class TestSubmodules(unittest.TestCase):
    def setUp(self):
        self.sub = make_repo({"lib.py": "def lib():\n    return 1\n"})
        self.addCleanup(self.sub.cleanup)
        self.repo = make_repo()
        self.addCleanup(self.repo.cleanup)
        env = {**os.environ, "GIT_ALLOW_PROTOCOL": "file"}
        added = subprocess.run(
            ["git", "-c", "protocol.file.allow=always", "submodule", "add", str(self.sub.path), "vendor/sub"],
            cwd=str(self.repo.path), capture_output=True, text=True, env=env,
        )
        if added.returncode != 0:
            self.skipTest(f"sous-modules locaux non disponibles : {added.stderr[:100]}")
        git(self.repo.path, *COMMIT, "ajoute un sous-module")

    def orchestrator(self):
        return MultiAgentOrchestrator(
            claude_client=ClaudeCliClient(mock_mode=True), jev_client=JevClient(mock_mode=True),
            project_dir=str(self.repo.path), run_tests=False, use_branch=True, doc_edit=False,
        )

    def test_a_repository_with_a_clean_submodule_works_normally(self):
        report = self.orchestrator().run(PROMPT)
        self.assertTrue(report.is_success)
        self.assertEqual(self.repo.current_branch(), "main")
        self.assertEqual(self.repo.status(), "")
        self.assertEqual(report.modified_files, ["src/components/TabX.tsx"])      # le sous-module n'est pas touché

    def test_a_modified_submodule_is_refused_cleanly_and_left_untouched(self):
        """`git stash` ne met pas à l'abri un sous-module modifié : on refuse plutôt que de risquer ce travail."""
        lib = self.repo.path / "vendor" / "sub" / "lib.py"
        lib.write_text("def lib():\n    return 'modifié par vous'\n", encoding="utf-8", newline="\n")

        with self.assertRaises(WorkflowPreconditionError) as ctx:
            self.orchestrator().run(PROMPT)

        self.assertIn("sous-module", str(ctx.exception))
        self.assertEqual(lib.read_text(encoding="utf-8"), "def lib():\n    return 'modifié par vous'\n")
        self.assertEqual(self.repo.current_branch(), "main")
        self.assertEqual([b for b in self.repo.branches() if b.startswith("workflow/")], [])
        self.assertEqual(self.repo.stashes(), [])


if __name__ == "__main__":
    unittest.main()

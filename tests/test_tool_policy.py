"""
Isolation cognitive réelle : ce que chaque rôle peut voir et faire (outils, répertoire de travail).
"""

import os
import unittest
from pathlib import Path

from gitrepo import make_repo

from workflow_claude.clients.claude_cli import ClaudeCliClient
from workflow_claude.clients.jev_client import JevClient
from workflow_claude.models import WorkflowType
from workflow_claude.orchestrator import MultiAgentOrchestrator
from workflow_claude.roles import Role
from workflow_claude.tool_policy import BASH_ALLOWED, BASH_DENIED, policy_for

PROMPT = "Modifie la fonction de tri dans l'onglet x"


class RecordingClaude(ClaudeCliClient):
    """Client simulé qui enregistre, pour chaque appel, le rôle, les options et le contenu de cwd."""

    def __init__(self):
        super().__init__(mock_mode=True)
        self.calls = []

    def run(self, prompt, model, **kwargs):
        cwd = kwargs.get("cwd")
        self.calls.append({
            "role": kwargs.get("role"),
            "kwargs": kwargs,
            "cwd": cwd,
            "cwd_entries": sorted(os.listdir(cwd)) if cwd and os.path.isdir(cwd) else None,
        })
        return super().run(prompt, model, **kwargs)

    def by_role(self, role):
        return [c for c in self.calls if c["role"] is role]


class ModerateJev(JevClient):
    def __init__(self):
        super().__init__(mock_mode=True)

    def classify(self, context, choices, question_label=""):
        return WorkflowType.MOYENNE.value if WorkflowType.SIMPLE.value in choices else choices[0]


class TestPolicyDefinitions(unittest.TestCase):
    def test_reviewers_have_no_tools(self):
        for role in (Role.QUALITY, Role.SECURITY, Role.FEEDBACK, Role.DOC):
            self.assertEqual(policy_for(role).tools, "", role)
            self.assertTrue(role.is_isolated_reviewer)
        self.assertFalse(Role.SPEC.is_isolated_reviewer)
        self.assertFalse(Role.DEV.is_isolated_reviewer)

    def test_spec_is_read_only(self):
        tools = policy_for(Role.SPEC).tools.split(",")
        self.assertEqual(sorted(tools), ["Glob", "Grep", "Read"])
        self.assertNotIn("Edit", tools)
        self.assertNotIn("Write", tools)
        self.assertNotIn("Bash", tools)

    def test_dev_edits_files_but_has_no_bash_by_default(self):
        policy = policy_for(Role.DEV)
        self.assertIn("Edit", policy.tools.split(","))
        self.assertNotIn("Bash", policy.tools.split(","))
        self.assertEqual(policy.permission_mode, "acceptEdits")
        self.assertEqual(policy.allowed_tools, [])

    def test_dev_with_bash_is_restricted_by_allow_and_deny_lists(self):
        policy = policy_for(Role.DEV, allow_bash=True)
        self.assertIn("Bash", policy.tools.split(","))
        self.assertEqual(policy.allowed_tools, BASH_ALLOWED)
        self.assertEqual(policy.disallowed_tools, BASH_DENIED)

    def test_git_commands_that_move_head_or_index_are_denied(self):
        for verb in ("push", "commit", "checkout", "switch", "reset", "clean", "stash", "branch", "merge", "rebase", "restore", "add"):
            self.assertIn(f"Bash(git {verb}:*)", BASH_DENIED, verb)
        for dangerous in ("rm", "sudo", "curl", "wget"):
            self.assertIn(f"Bash({dangerous}:*)", BASH_DENIED, dangerous)

    def test_allow_list_does_not_contradict_deny_list(self):
        self.assertEqual(set(BASH_ALLOWED) & set(BASH_DENIED), set())
        for rule in BASH_ALLOWED + BASH_DENIED:
            self.assertRegex(rule, r"^Bash\([^()&|<>^%\"]+:\*\)$")  # format valide et sans caractère dangereux pour cmd


class TestOrchestratorAppliesThePolicy(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        self.addCleanup(self.repo.cleanup)

    def run_in_repo(self, **kwargs):
        claude = RecordingClaude()
        options = dict(
            claude_client=claude,
            jev_client=ModerateJev(),
            project_dir=str(self.repo.path),
            run_tests=False,
            use_branch=True,
        )
        options.update(kwargs)
        report = MultiAgentOrchestrator(**options).run(PROMPT)
        self.assertTrue(report.is_success)
        return claude

    def test_in_repo_roles_get_their_tools_and_working_directory(self):
        claude = self.run_in_repo()

        (spec,) = claude.by_role(Role.SPEC)
        self.assertEqual(spec["kwargs"]["tools"], "Read,Grep,Glob")
        self.assertEqual(Path(spec["cwd"]).resolve(), self.repo.path)

        dev = claude.by_role(Role.DEV)[0]
        self.assertEqual(dev["kwargs"]["tools"], "Read,Edit,Write,Grep,Glob")
        self.assertEqual(dev["kwargs"]["permission_mode"], "acceptEdits")
        self.assertNotIn("allowed_tools", dev["kwargs"])
        self.assertEqual(Path(dev["cwd"]).resolve(), self.repo.path)

        for role in (Role.QUALITY, Role.SECURITY, Role.DOC):
            calls = claude.by_role(role)
            self.assertTrue(calls, f"aucun appel pour {role}")
            for call in calls:
                self.assertEqual(call["kwargs"]["tools"], "", role)
                self.assertNotEqual(Path(call["cwd"]).resolve(), self.repo.path, role)
                self.assertEqual(call["cwd_entries"], [], f"{role} voit des fichiers : {call['cwd_entries']}")

    def test_isolated_directory_is_removed_after_the_run(self):
        claude = self.run_in_repo()
        isolated = {c["cwd"] for c in claude.calls if c["role"].is_isolated_reviewer}
        self.assertEqual(len(isolated), 1)  # un seul répertoire vide partagé pendant tout le run
        self.assertFalse(Path(isolated.pop()).exists())

    def test_allow_bash_passes_the_restricted_lists_to_the_dev_agent_only(self):
        claude = self.run_in_repo(allow_bash=True)

        dev = claude.by_role(Role.DEV)[0]["kwargs"]
        self.assertIn("Bash", dev["tools"].split(","))
        self.assertEqual(dev["allowed_tools"], BASH_ALLOWED)
        self.assertIn("Bash(git push:*)", dev["disallowed_tools"])

        for role in (Role.SPEC, Role.QUALITY, Role.SECURITY, Role.DOC):
            for call in claude.by_role(role):
                self.assertNotIn("Bash", call["kwargs"]["tools"])
                self.assertNotIn("allowed_tools", call["kwargs"])

    def test_standalone_mode_uses_no_tools_and_an_empty_directory_everywhere(self):
        claude = RecordingClaude()
        orchestrator = MultiAgentOrchestrator(
            claude_client=claude, jev_client=ModerateJev(), standalone_mode=True, project_dir=str(self.repo.path)
        )
        report = orchestrator.run("Crée un service de paiement")

        self.assertTrue(report.is_success)
        self.assertGreaterEqual(len(claude.calls), 5)
        cwds = set()
        for call in claude.calls:
            self.assertEqual(call["kwargs"]["tools"], "")
            self.assertEqual(call["cwd_entries"], [])
            cwds.add(call["cwd"])
        self.assertFalse(Path(next(iter(cwds))).exists())


if __name__ == "__main__":
    unittest.main()

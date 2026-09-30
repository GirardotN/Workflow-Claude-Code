"""
Moteur unique : les deux modes suivent la même machine à états ; contrôle de contexte (diff filtré, plafonds),
métadonnées de coût par étape, avertissements sur les replis de routage.
"""

import logging
import unittest

from gitrepo import make_repo

from workflow_claude.clients.claude_cli import ClaudeCliClient, ClaudeResult
from workflow_claude.clients.git_client import DEFAULT_DIFF_EXCLUDES, GitClient, diff_exclude_specs
from workflow_claude.clients.jev_client import JevClient
from workflow_claude.fsm import InRepoBackend, StandaloneBackend, WorkflowEngine
from workflow_claude.models import DevSpecialty, WorkflowType
from workflow_claude.orchestrator import MultiAgentOrchestrator
from workflow_claude.roles import Role


class ModerateJev(JevClient):
    def __init__(self, complexity):
        super().__init__(mock_mode=True)
        self.complexity = complexity

    def classify(self, context, choices, question_label="", **kwargs):
        return self.complexity if self.complexity in choices else choices[0]


class CostClaude(ClaudeCliClient):
    """Claude simulé qui expose un `last_result` comme le vrai client (coût, tours, session)."""

    def __init__(self):
        super().__init__(mock_mode=True)
        self.calls = 0

    def run(self, prompt, model, **kwargs):
        text = super().run(prompt, model, **kwargs)
        self.calls += 1
        self.last_result = ClaudeResult(text=text, cost_usd=0.01, num_turns=2, duration_ms=500, session_id=f"s{self.calls}")
        return text


def step_names(report):
    return [s.step_name for s in report.history]


class TestSameStateMachineForBothModes(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        self.addCleanup(self.repo.cleanup)

    def run_both(self, complexity):
        common = dict(claude_client=ClaudeCliClient(mock_mode=True), run_tests=False)
        standalone = MultiAgentOrchestrator(jev_client=ModerateJev(complexity), standalone_mode=True, **common).run("Crée un module")
        in_repo = MultiAgentOrchestrator(
            jev_client=ModerateJev(complexity), project_dir=str(self.repo.path), use_branch=True, **common
        ).run("Modifie le tri de l'onglet x")
        self.assertTrue(standalone.is_success and in_repo.is_success)
        return standalone, in_repo

    def test_same_sequence_of_roles_and_models_for_each_complexity(self):
        for complexity in (WorkflowType.SIMPLE, WorkflowType.MOYENNE, WorkflowType.COMPLEXE):
            with self.subTest(complexity=complexity.name):
                standalone, in_repo = self.run_both(complexity.value)
                self.assertEqual(standalone.workflow_type, in_repo.workflow_type)
                # même enchaînement de modèles, étape par étape (les noms diffèrent, pas la logique)
                self.assertEqual([s.model for s in standalone.history], [s.model for s in in_repo.history])
                security = [n for n in step_names(in_repo) if "SECU" in n]
                self.assertEqual(bool(security), complexity is not WorkflowType.SIMPLE)

    def test_in_repo_and_standalone_step_names_keep_their_contract(self):
        standalone, in_repo = self.run_both(WorkflowType.MOYENNE.value)
        self.assertEqual(step_names(standalone), [
            "1_GENERATION_SPEC", "DEV_CYCLE_1", "CHECK_QUALITE_CYCLE_1", "CHECK_SECU_CYCLE_1", "FINAL_DOC_ET_COMMIT"])
        self.assertEqual(step_names(in_repo), [
            "1_EXPLORATION_ET_SPEC", "DEV_IN_SITU_CYCLE_1", "CHECK_QUALITE_DIFF_CYCLE_1", "CHECK_SECU_DIFF_CYCLE_1", "FINAL_DOC_ET_COMMIT"])

    def test_backends_declare_their_mode(self):
        orchestrator = MultiAgentOrchestrator(claude_client=ClaudeCliClient(mock_mode=True), jev_client=ModerateJev("x"))
        self.assertTrue(InRepoBackend(orchestrator).in_repo)
        self.assertFalse(InRepoBackend(orchestrator).text_only)
        self.assertFalse(StandaloneBackend(orchestrator).in_repo)
        self.assertTrue(StandaloneBackend(orchestrator).text_only)
        self.assertTrue(WorkflowEngine)


class TestStepMetadata(unittest.TestCase):
    def test_cost_and_turns_are_attached_to_each_claude_step_and_summed(self):
        claude = CostClaude()
        report = MultiAgentOrchestrator(
            claude_client=claude, jev_client=ModerateJev(WorkflowType.MOYENNE.value), standalone_mode=True
        ).run("Crée un module")

        claude_steps = [s for s in report.history]
        self.assertEqual(len(claude_steps), 5)
        for step in claude_steps:
            self.assertEqual(step.metadata["cost_usd"], 0.01, step.step_name)
            self.assertEqual(step.metadata["num_turns"], 2)
        # une session distincte par appel : aucune métadonnée « périmée » n'est réutilisée
        self.assertEqual(len({s.metadata["session_id"] for s in claude_steps}), 5)
        self.assertAlmostEqual(report.cost_usd, 0.05)

    def test_clients_without_last_result_are_supported(self):
        report = MultiAgentOrchestrator(
            claude_client=ClaudeCliClient(mock_mode=True), jev_client=ModerateJev("x"), standalone_mode=True
        ).run("Crée un module")
        self.assertEqual(report.cost_usd, 0.0)
        self.assertTrue(all("cost_usd" not in s.metadata for s in report.history))


class TestDiffFiltering(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo({"package-lock.json": "{}\n", "dist/app.js": "old\n"})
        self.addCleanup(self.repo.cleanup)
        self.git = GitClient(str(self.repo.path))

    def test_lockfiles_and_generated_files_are_excluded_from_the_reviewed_diff(self):
        self.repo.write("package-lock.json", '{"lockfileVersion": 3}\n')
        self.repo.write("dist/app.js", "minified new\n")
        self.repo.write("web/bundle.min.js", "x\n")
        self.repo.write("src/feature.py", "def feature():\n    return 1\n")

        diff, excluded = self.git.get_diff_split()

        self.assertIn("src/feature.py", diff)
        self.assertNotIn("package-lock.json", diff)
        self.assertNotIn("minified new", diff)
        self.assertNotIn("bundle.min.js", diff)
        self.assertEqual(excluded, ["dist/app.js", "package-lock.json", "web/bundle.min.js"])

    def test_nested_lockfiles_match_at_any_depth(self):
        self.repo.write("apps/web/yarn.lock", "v1\n")
        diff, excluded = self.git.get_diff_split()
        self.assertEqual(diff, "")
        self.assertEqual(excluded, ["apps/web/yarn.lock"])

    def test_no_exclusion_when_asked(self):
        self.repo.write("package-lock.json", "{}\n\n")
        diff, excluded = self.git.get_diff_split(exclude=[])
        self.assertIn("package-lock.json", diff)
        self.assertEqual(excluded, [])

    def test_specs_format(self):
        self.assertEqual(diff_exclude_specs(["yarn.lock"]), [":(exclude,glob)**/yarn.lock"])
        self.assertEqual(diff_exclude_specs(["dist/"]), [":(exclude,glob)**/dist/**"])
        self.assertIn("package-lock.json", DEFAULT_DIFF_EXCLUDES)

    def test_orchestrator_tells_the_reviewers_what_was_left_out(self):
        self.repo.write("package-lock.json", '{"lockfileVersion": 3}\n')
        seen = []

        class SpyClaude(ClaudeCliClient):
            def run(self, prompt, model, **kwargs):
                if kwargs.get("role") is Role.QUALITY:
                    seen.append(prompt)
                return super().run(prompt, model, **kwargs)

        MultiAgentOrchestrator(
            claude_client=SpyClaude(mock_mode=True), jev_client=ModerateJev("x"), project_dir=str(self.repo.path),
            run_tests=False, use_branch=True,
        ).run("Modifie le tri de l'onglet x")

        self.assertTrue(seen)
        self.assertIn("fichier(s) généré(s) ou volumineux exclu(s)", seen[0])
        self.assertIn("package-lock.json", seen[0])
        self.assertNotIn('"lockfileVersion"', seen[0])


class TestPromptCaps(unittest.TestCase):
    def test_huge_code_is_truncated_in_reviewer_prompts_but_kept_in_the_report(self):
        big_code = "def g():\n    return 'x'\n" * 40000  # ~880 000 caractères

        class BigCodeClaude(ClaudeCliClient):
            def run(self, prompt, model, **kwargs):
                return big_code if kwargs.get("role") is Role.DEV else super().run(prompt, model, **kwargs)

        report = MultiAgentOrchestrator(
            claude_client=BigCodeClaude(mock_mode=True), jev_client=ModerateJev("x"), standalone_mode=True,
            max_prompt_chars=20_000, jev_max_chars=20_000,
        ).run("Crée un module")

        self.assertTrue(report.is_success)
        self.assertEqual(report.code_produit, big_code)  # le rapport garde tout
        for step in report.history:
            if "CHECK_QUALITE" in step.step_name or "FINAL_DOC" in step.step_name:
                self.assertLess(len(step.prompt_sent), 22_000, step.step_name)
                self.assertIn("caractères omis", step.prompt_sent)


class TestRoutingFallbackWarnings(unittest.TestCase):
    def test_unrecognized_routing_answers_are_logged(self):
        with self.assertLogs("models", level=logging.WARNING) as logs:
            self.assertEqual(WorkflowType.from_str("n'importe quoi"), WorkflowType.MOYENNE)
            self.assertEqual(DevSpecialty.from_str("cobol"), DevSpecialty.PYTHON)
        joined = "\n".join(logs.output)
        self.assertIn("Niveau de complexité non reconnu", joined)
        self.assertIn("Spécialité non reconnue", joined)

    def test_recognized_answers_do_not_warn(self):
        with self.assertNoLogs("models", level=logging.WARNING):
            WorkflowType.from_str("Tâche Complexe")
            DevSpecialty.from_str("Dev C#")


if __name__ == "__main__":
    unittest.main()

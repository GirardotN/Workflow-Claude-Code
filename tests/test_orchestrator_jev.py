"""
Jev dans l'orchestrateur : confidentialité des données envoyées, trace des décisions, contrôle de
cohérence avec le verdict des relecteurs, arrêt propre sur erreur Jev.
"""

import logging
import unittest

from gitrepo import make_repo

from workflow_claude.clients.claude_cli import ClaudeCliClient
from workflow_claude.clients.jev_client import JevApiError, JevClient, JevDecision
from workflow_claude.models import WorkflowType
from workflow_claude.orchestrator import COMPLEXITY_DESCRIPTIONS, VERDICT_INSTRUCTION, MultiAgentOrchestrator
from workflow_claude.roles import Role

PROMPT = "Modifie la fonction de tri dans l'onglet x"
SECRET_CODE = "API_TOKEN = 'supersecretvalue123'\npassword = 'hunter2hunter2'\ndef f():\n    return 1\n"
JEV_KEY = "tsk-LIVE-KEY-ABCDEFGHIJ1234567890"


class ScriptedClaude(ClaudeCliClient):
    """Claude simulé : code produit contenant des secrets, revues au verdict configurable."""

    def __init__(self, verdict="PASS", code=SECRET_CODE, review_extra=""):
        super().__init__(mock_mode=True)
        self.verdict = verdict
        self.code = code
        self.review_extra = review_extra

    def run(self, prompt, model, **kwargs):
        role = kwargs.get("role")
        if role is Role.DEV:
            return self.code
        if role in (Role.QUALITY, Role.SECURITY):
            return f"Analyse effectuée. {self.review_extra}\nVERDICT: {self.verdict}"
        return super().run(prompt, model, **kwargs)


class RecordingJev(JevClient):
    """Jev en simulation qui enregistre ce qu'on lui envoie."""

    def __init__(self, complexity=WorkflowType.SIMPLE.value, validate_result=True):
        super().__init__(api_key=JEV_KEY, mock_mode=True)
        self.complexity = complexity
        self.validate_result = validate_result
        self.contexts = []
        self.classify_calls = []

    def classify(self, context, choices, question_label="", descriptions=None):
        self.classify_calls.append({"context": context, "choices": choices, "descriptions": descriptions})
        choice = self.complexity if self.complexity in choices else choices[0]
        self.last_decision = JevDecision(kind="choice", mode="mock", question=question_label, result=choice, value=0.9)
        return choice

    def validate(self, context, criteria, threshold=None):
        self.contexts.append(context)
        self.last_decision = JevDecision(
            kind="noul", mode="mock", question=criteria, result=self.validate_result, value=0.77, threshold=0.5
        )
        return self.validate_result


def standalone(claude, jev, **kwargs):
    return MultiAgentOrchestrator(claude_client=claude, jev_client=jev, standalone_mode=True, **kwargs)


class TestDataSentToJev(unittest.TestCase):
    def test_secrets_are_masked_in_every_validation_context(self):
        jev = RecordingJev(complexity=WorkflowType.MOYENNE.value)
        report = standalone(ScriptedClaude(review_extra=f"La clé {JEV_KEY} est visible."), jev).run("Crée un module")

        self.assertTrue(report.is_success)
        self.assertGreaterEqual(len(jev.contexts), 2)  # qualité + sécurité
        for context in jev.contexts:
            self.assertNotIn("supersecretvalue123", context)
            self.assertNotIn("hunter2hunter2", context)
            self.assertNotIn(JEV_KEY, context)                # la clé TypeSafe elle-même n'est jamais renvoyée
            self.assertIn("[REDACTED", context)
            self.assertIn("def f():", context)                # le reste du code est bien transmis

    def test_review_only_mode_sends_no_code(self):
        jev = RecordingJev()
        report = standalone(ScriptedClaude(), jev, jev_send="review-only").run("Crée un module")

        self.assertTrue(report.is_success)
        for context in jev.contexts:
            self.assertTrue(context.startswith("Review Qualité :"))
            self.assertNotIn("def f():", context)
            self.assertNotIn("Code :", context)

    def test_routing_context_is_masked_and_descriptions_are_sent(self):
        jev = RecordingJev()
        standalone(ScriptedClaude(), jev).run("Crée un module")
        first = jev.classify_calls[0]
        self.assertEqual(first["descriptions"], COMPLEXITY_DESCRIPTIONS)
        self.assertNotIn("supersecretvalue123", first["context"])

    def test_invalid_jev_send_is_refused(self):
        with self.assertRaises(ValueError):
            MultiAgentOrchestrator(claude_client=ScriptedClaude(), jev_client=RecordingJev(), jev_send="everything")

    def test_large_diff_is_truncated_to_the_budget(self):
        big_code = "def g():\n    return 'x'\n" * 20000  # ~440 000 caractères
        jev = RecordingJev()
        report = standalone(ScriptedClaude(code=big_code), jev, jev_max_chars=30000).run("Crée un module")
        self.assertTrue(report.is_success)
        self.assertTrue(all(len(c) <= 30000 for c in jev.contexts))
        self.assertTrue(any("caractères omis" in c for c in jev.contexts))


class TestDecisionTrace(unittest.TestCase):
    def test_decisions_are_recorded_in_the_report(self):
        report = standalone(ScriptedClaude(), RecordingJev(complexity=WorkflowType.MOYENNE.value)).run("Crée un module")

        steps = [d["step"] for d in report.decisions]
        self.assertEqual(steps[:2], ["ROUTAGE_COMPLEXITE", "ROUTAGE_SPECIALITE"])
        self.assertIn("VALIDATION_QUALITE_CYCLE_1", steps)
        self.assertIn("VALIDATION_SECU_CYCLE_1", steps)
        quality = next(d for d in report.decisions if d["step"] == "VALIDATION_QUALITE_CYCLE_1")
        self.assertEqual((quality["kind"], quality["result"], quality["value"]), ("noul", True, 0.77))
        self.assertEqual(report.jev_mode, "mock")

    def test_doubles_without_last_decision_are_supported(self):
        class BareJev:
            def classify(self, context, choices, question_label=""):
                return choices[0]

            def validate(self, context, criteria):
                return True

        report = standalone(ScriptedClaude(), BareJev()).run("Crée un module")
        self.assertTrue(report.is_success)
        self.assertEqual(report.decisions, [])
        self.assertEqual(report.jev_mode, "")


class TestVerdictConsistency(unittest.TestCase):
    def test_review_prompts_ask_for_a_verdict(self):
        report = standalone(ScriptedClaude(), RecordingJev(complexity=WorkflowType.MOYENNE.value)).run("Crée un module")
        for step in report.history:
            if "CHECK_QUALITE" in step.step_name or "CHECK_SECU" in step.step_name:
                self.assertIn("VERDICT: PASS", step.prompt_sent, step.step_name)
                self.assertTrue(step.prompt_sent.endswith(VERDICT_INSTRUCTION) or VERDICT_INSTRUCTION in step.prompt_sent)

    def test_contradiction_is_logged_and_recorded_but_jev_decides(self):
        claude = ScriptedClaude(verdict="FAIL")
        jev = RecordingJev(validate_result=True)
        with self.assertLogs("orchestrator", level=logging.WARNING) as logs:
            report = standalone(claude, jev).run("Crée un module")

        self.assertTrue(report.is_success)  # la décision de Jev s'applique
        self.assertIn("Incohérence", "\n".join(logs.output))
        quality = next(d for d in report.decisions if d["step"] == "VALIDATION_QUALITE_CYCLE_1")
        self.assertEqual(quality["reviewer_verdict"], "FAIL")
        self.assertFalse(quality["consistent"])

    def test_agreement_is_recorded_without_warning(self):
        report = standalone(ScriptedClaude(verdict="PASS"), RecordingJev(validate_result=True)).run("Crée un module")
        quality = next(d for d in report.decisions if d["step"] == "VALIDATION_QUALITE_CYCLE_1")
        self.assertTrue(quality["consistent"])


class FailingJev(RecordingJev):
    def validate(self, context, criteria, threshold=None):
        raise JevApiError("TypeSafe Jev indisponible (HTTP 503)")


class TestJevFailureStopsCleanly(unittest.TestCase):
    def test_jev_error_aborts_and_restores_the_repository(self):
        repo = make_repo()
        self.addCleanup(repo.cleanup)
        repo.write("wip.txt", "travail en cours\n")
        head = repo.git("rev-parse", "HEAD")

        orchestrator = MultiAgentOrchestrator(
            claude_client=ClaudeCliClient(mock_mode=True),
            jev_client=FailingJev(),
            project_dir=str(repo.path),
            run_tests=False,
            use_branch=True,
        )
        with self.assertRaises(JevApiError):
            orchestrator.run(PROMPT)

        self.assertEqual(repo.current_branch(), "main")
        self.assertEqual(repo.git("rev-parse", "HEAD"), head)
        self.assertEqual([b for b in repo.branches() if b.startswith("workflow/")], [])
        self.assertEqual(repo.read("wip.txt"), "travail en cours\n")
        self.assertEqual(repo.stashes(), [])
        self.assertEqual(repo.status(), "?? wip.txt")

    def test_jev_error_is_never_turned_into_a_validation(self):
        with self.assertRaises(JevApiError):
            standalone(ScriptedClaude(), FailingJev()).run("Crée un module")


if __name__ == "__main__":
    unittest.main()

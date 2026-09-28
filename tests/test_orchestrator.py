"""
Suite de tests unitaires et de validation du workflow multi-agents.
Vérifie rigoureusement chaque branche (Simple, Moyenne, Complexe),
les modèles assignés, l'isolation des contextes et les boucles de feedback.
"""

import unittest
from unittest.mock import MagicMock

from clients.claude_cli import ClaudeCliClient
from clients.jev_client import JevClient
from models import DevSpecialty, WorkflowType
from orchestrator import (
    MultiAgentOrchestrator,
    WorkflowMaxRetriesExceeded,
    clean_code_output,
)


class MockClaudeClient:
    """Mock contrôlé du client Claude pour tester les transitions d'état."""

    def __init__(self):
        self.calls = []
        self.response_generator = None

    def run(self, prompt: str, model: str, **kwargs) -> str:
        self.calls.append({"prompt": prompt, "model": model})
        if self.response_generator:
            return self.response_generator(prompt, model)
        return f"Response from {model}"


class MockJevClient:
    """Mock contrôlé de Jev pour simuler classifications et validations."""

    def __init__(self):
        self.classification_type = WorkflowType.SIMPLE.value
        self.specialty = DevSpecialty.PYTHON.value
        self.quality_validations = [True]
        self.security_validations = [True]
        self.quality_call_count = 0
        self.security_call_count = 0

    def classify(self, context: str, choices: list, question_label: str = "") -> str:
        if WorkflowType.SIMPLE.value in choices:
            return self.classification_type
        if DevSpecialty.PYTHON.value in choices:
            return self.specialty
        return choices[0]

    def validate(self, context: str, criteria: str) -> bool:
        crit_lower = criteria.lower()
        if "qualité" in crit_lower or "bug" in crit_lower:
            idx = min(self.quality_call_count, len(self.quality_validations) - 1)
            res = self.quality_validations[idx]
            self.quality_call_count += 1
            return res
        if "sécurisé" in crit_lower or "sécurité" in crit_lower:
            idx = min(self.security_call_count, len(self.security_validations) - 1)
            res = self.security_validations[idx]
            self.security_call_count += 1
            return res
        return True


class TestMultiAgentWorkflow(unittest.TestCase):

    def setUp(self):
        self.claude = MockClaudeClient()
        self.jev = MockJevClient()
        self.orchestrator = MultiAgentOrchestrator(
            claude_client=self.claude,
            jev_client=self.jev,
            max_retries=4,
        )

    def test_branche_simple_success(self):
        """
        Branche Tâche Simple :
        - Spec : Sonnet
        - Dev : Sonnet
        - Qualité : Sonnet -> Validé par Jev
        - Sécurité : NON exécuté
        - Doc & Commit : Sonnet
        """
        self.jev.classification_type = WorkflowType.SIMPLE.value
        self.jev.quality_validations = [True]

        report = self.orchestrator.run("Créer un filtre d'adresses email")

        self.assertTrue(report.is_success)
        self.assertEqual(report.workflow_type, WorkflowType.SIMPLE)
        self.assertEqual(report.iterations_count, 1)

        # Modèles utilisés
        step_models = {s.step_name: s.model for s in report.history}
        self.assertEqual(step_models["1_GENERATION_SPEC"], "sonnet")
        self.assertEqual(step_models["DEV_CYCLE_1"], "sonnet")
        self.assertEqual(step_models["CHECK_QUALITE_CYCLE_1"], "sonnet")
        self.assertEqual(step_models["FINAL_DOC_ET_COMMIT"], "sonnet")

        # Vérifier que le check sécurité N'A PAS été exécuté
        secu_steps = [s for s in report.history if "SECU" in s.step_name]
        self.assertEqual(len(secu_steps), 0)

    def test_branche_moyenne_with_security_rejection_and_retry(self):
        """
        Branche Tâche Moyenne avec rejet sécurité :
        - Cycle 1 : Dev (Sonnet) -> Qualité (Sonnet, Validé) -> Sécurité (Sonnet, REJETÉ)
        - Feedback sécurité : Haiku
        - Cycle 2 : Dev (Sonnet) -> Qualité (Sonnet, Validé) -> Sécurité (Sonnet, Validé)
        - Doc & Commit : Haiku
        """
        self.jev.classification_type = WorkflowType.MOYENNE.value
        self.jev.quality_validations = [True, True]
        self.jev.security_validations = [False, True]  # Rejet au 1er cycle, validation au 2e

        report = self.orchestrator.run("Créer une API de paiement avec webhook Stripe")

        self.assertTrue(report.is_success)
        self.assertEqual(report.workflow_type, WorkflowType.MOYENNE)
        self.assertEqual(report.iterations_count, 2)

        # Vérifier la séquence des étapes
        step_names = [s.step_name for s in report.history]
        self.assertIn("DEV_CYCLE_1", step_names)
        self.assertIn("CHECK_QUALITE_CYCLE_1", step_names)
        self.assertIn("CHECK_SECU_CYCLE_1", step_names)
        self.assertIn("FEEDBACK_SECU_CYCLE_1", step_names)
        self.assertIn("DEV_CYCLE_2", step_names)
        self.assertIn("CHECK_QUALITE_CYCLE_2", step_names)
        self.assertIn("CHECK_SECU_CYCLE_2", step_names)
        self.assertIn("FINAL_DOC_ET_COMMIT", step_names)

        # Modèles pour Tâche Moyenne
        step_models = {s.step_name: s.model for s in report.history}
        self.assertEqual(step_models["FEEDBACK_SECU_CYCLE_1"], "haiku")
        self.assertEqual(step_models["FINAL_DOC_ET_COMMIT"], "haiku")

    def test_branche_complexe_with_quality_rejection_and_retry(self):
        """
        Branche Tâche Complexe avec rejet qualité :
        - Modèle Dev : Opus
        - Modèle Qualité : Opus -> REJET
        - Feedback Qualité : Sonnet
        - Cycle 2 : Dev Opus -> Qualité Opus (Validé) -> Sécurité Sonnet (Validé)
        - Doc & Commit : Haiku
        """
        self.jev.classification_type = WorkflowType.COMPLEXE.value
        self.jev.quality_validations = [False, True]
        self.jev.security_validations = [True]

        report = self.orchestrator.run("Moteur de calcul distribué pour analyse financière")

        self.assertTrue(report.is_success)
        self.assertEqual(report.workflow_type, WorkflowType.COMPLEXE)
        self.assertEqual(report.iterations_count, 2)

        step_models = {s.step_name: s.model for s in report.history}
        # Tâche Complexe : Dev et Qualité doivent être OPUS
        self.assertEqual(step_models["DEV_CYCLE_1"], "opus")
        self.assertEqual(step_models["CHECK_QUALITE_CYCLE_1"], "opus")
        # Feedback Qualité doit être SONNET
        self.assertEqual(step_models["FEEDBACK_QUALITE_CYCLE_1"], "sonnet")
        self.assertEqual(step_models["DEV_CYCLE_2"], "opus")
        self.assertEqual(step_models["CHECK_SECU_CYCLE_2"], "sonnet")
        # Doc & Commit pour Complexe doit être HAIKU
        self.assertEqual(step_models["FINAL_DOC_ET_COMMIT"], "haiku")

    def test_context_isolation_constraints(self):
        """
        Vérifie l'isolation stricte des contextes :
        - Check Qualité ne reçoit QUE le code produit (pas le rôle, pas la spec initiale).
        - Check Sécurité reçoit le code ET la review qualité.
        """
        self.jev.classification_type = WorkflowType.MOYENNE.value
        self.jev.quality_validations = [True]
        self.jev.security_validations = [True]

        report = self.orchestrator.run("Générer un module de chiffrement AES-256")

        # Trouver l'étape check qualité
        qual_step = next(s for s in report.history if "CHECK_QUALITE" in s.step_name)
        self.assertIn("CODE À ANALYSER :", qual_step.prompt_sent)
        self.assertNotIn("CAHIER DES CHARGES :", qual_step.prompt_sent)

        # Trouver l'étape check sécurité
        secu_step = next(s for s in report.history if "CHECK_SECU" in s.step_name)
        self.assertIn("CODE PRODUIT :", secu_step.prompt_sent)
        self.assertIn("REVIEW QUALITÉ PRÉALABLE :", secu_step.prompt_sent)

    def test_circuit_breaker_max_retries(self):
        """
        Vérifie que la boucle s'arrête proprement lorsque MAX_RETRIES est atteint
        sans provoquer de boucle infinie.
        """
        self.jev.classification_type = WorkflowType.SIMPLE.value
        self.jev.quality_validations = [False, False, False, False, False]
        self.orchestrator.max_retries = 3

        report = self.orchestrator.run("Tâche qui échoue continuellement")

        self.assertFalse(report.is_success)
        self.assertEqual(report.iterations_count, 3)
        self.assertIn("Circuit breaker", report.error_message)

    def test_circuit_breaker_raise_on_failure(self):
        """
        Vérifie que WorkflowMaxRetriesExceeded est levée si raise_on_failure=True.
        """
        self.jev.classification_type = WorkflowType.SIMPLE.value
        self.jev.quality_validations = [False, False, False, False, False]
        self.orchestrator.max_retries = 2

        with self.assertRaises(WorkflowMaxRetriesExceeded):
            self.orchestrator.run("Tâche qui échoue", raise_on_failure=True)

    def test_dev_specialty_no_false_positive_on_ui(self):
        """
        Vérifie que des termes français contenant 'ui' (requis, construire, suivant)
        ne sont pas faussement classés en Dev UI.
        """
        jev = JevClient(mock_mode=True)
        choices = ["Dev C#", "Dev Node.js", "Dev UI", "Dev Python"]

        spec_text = "Composants requis : gestionnaire de calculs suivant les normes"
        classified = jev.classify(spec_text, choices)
        self.assertNotEqual(classified, "Dev UI")

        # Test direct dans models.py
        self.assertNotEqual(DevSpecialty.from_str("module de traitement requis"), DevSpecialty.UI)
        self.assertEqual(DevSpecialty.from_str("composant UI react"), DevSpecialty.UI)

    def test_clean_code_output_strips_markdown_fences(self):
        """
        Vérifie que les balises markdown ```python sont correctement retirées
        et que le langage est détecté.
        """
        raw_markdown = "```python\ndef hello():\n    return 'world'\n```"
        code, lang = clean_code_output(raw_markdown)
        self.assertEqual(lang, "python")
        self.assertNotIn("```", code)
        self.assertIn("def hello():", code)

        plain_code = "const x = 42;"
        code2, lang2 = clean_code_output(plain_code)
        self.assertIsNone(lang2)
        self.assertEqual(code2, plain_code)

    def test_mock_validate_prioritizes_critical_rejection(self):
        """
        Vérifie que _mock_validate rejette un code comportant une vulnérabilité critique
        même s'il mentionne 'aucun bug'.
        """
        jev = JevClient(mock_mode=True)
        review = "Aucun bug de syntaxe détecté, mais présence d'une faille critique d'injection."
        is_valid = jev._mock_validate(review, "criteria")
        self.assertFalse(is_valid)

    def test_claude_cli_extract_result_with_braces(self):
        """
        Vérifie que l'extraction JSON de ClaudeCliClient n'est pas corrompue
        par du code contenant des accolades.
        """
        client = ClaudeCliClient(mock_mode=False)
        raw_output = '{"result": "class Foo { int x = {1}; }"}'
        extracted = client._extract_result(raw_output)
        self.assertEqual(extracted, "class Foo { int x = {1}; }")


if __name__ == "__main__":
    unittest.main()

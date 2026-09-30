"""
La politique de modèles est la source de vérité de la matrice : elle doit correspondre, cellule par
cellule, au tableau de docs/architecture.md. Les prompts respectent leurs contrats d'isolation.
"""

import re
import unittest
from pathlib import Path

from workflow_claude import prompts
from workflow_claude.models import DevSpecialty, WorkflowType
from workflow_claude.policy import ModelPolicy
from workflow_claude.roles import Role
from workflow_claude.tool_policy import policy_for

ARCHITECTURE_DOC = Path(__file__).resolve().parent.parent / "docs" / "architecture.md"

# (libellé dans la doc) -> attribut de ModelPolicy
DOC_ROWS = {
    "1. Spécification Technique": "spec",
    "3. Agent de Développement": "dev",
    "5. Audit Bug & Qualité": "quality",
    "7. Synthèse Feedback Qualité": "feedback_quality",
    "8. Audit Cyber-Sécurité": "security",
    "10. Synthèse Feedback Sécu": "feedback_security",
    "11. Documentation & Commit": "doc",
}
TYPES = (WorkflowType.SIMPLE, WorkflowType.MOYENNE, WorkflowType.COMPLEXE)


def parse_doc_matrix():
    """Lit le tableau de la matrice : {attribut: [simple, moyenne, complexe]} (None = non exécuté)."""
    matrix = {}
    for line in ARCHITECTURE_DOC.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| **"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        label = cells[0].strip("*").strip()
        if label not in DOC_ROWS or len(cells) < 4:
            continue
        values = []
        for cell in cells[1:4]:
            text = cell.replace("*", "").strip().lower()
            values.append(None if "non exécuté" in text else text)
        matrix[DOC_ROWS[label]] = values
    return matrix


class TestModelPolicyMatchesDocumentation(unittest.TestCase):
    def test_every_documented_row_was_found(self):
        self.assertEqual(set(parse_doc_matrix()), set(DOC_ROWS.values()))

    def test_policy_equals_the_documented_matrix(self):
        for attribute, expected_by_type in parse_doc_matrix().items():
            for workflow_type, expected in zip(TYPES, expected_by_type):
                policy = ModelPolicy.for_type(workflow_type)
                with self.subTest(row=attribute, type=workflow_type.name):
                    if expected is None:
                        self.assertFalse(policy.run_security)
                    else:
                        self.assertEqual(getattr(policy, attribute), expected)

    def test_security_runs_only_for_medium_and_complex(self):
        self.assertFalse(ModelPolicy.for_type(WorkflowType.SIMPLE).run_security)
        self.assertTrue(ModelPolicy.for_type(WorkflowType.MOYENNE).run_security)
        self.assertTrue(ModelPolicy.for_type(WorkflowType.COMPLEXE).run_security)

    def test_policy_is_immutable(self):
        policy = ModelPolicy.for_type(WorkflowType.SIMPLE)
        with self.assertRaises(Exception):
            policy.dev = "opus"


class TestPromptContracts(unittest.TestCase):
    SPEC = "CAHIER_DES_CHARGES_SECRET_DU_DEV"

    def test_quality_prompts_never_contain_the_spec_or_the_developer_role(self):
        for prompt in (prompts.quality_in_repo("+x = 1", 10_000), prompts.quality_standalone("x = 1", 10_000)):
            self.assertNotIn("CAHIER DES CHARGES", prompt)
            self.assertNotIn("Tu es un expert", prompt)
            self.assertIn("Senior Code Reviewer", prompt)
            self.assertTrue(prompt.endswith(prompts.VERDICT_INSTRUCTION))
            self.assertIn("VERDICT: PASS", prompt)
            self.assertIn("VERDICT: FAIL", prompt)

    def test_quality_prompt_labels(self):
        self.assertIn("GIT DIFF :\n+x = 1", prompts.quality_in_repo("+x = 1", 10_000))
        self.assertIn("CODE À ANALYSER :\nx = 1", prompts.quality_standalone("x = 1", 10_000))

    def test_security_prompts_receive_the_code_and_the_quality_review(self):
        for prompt, label in (
            (prompts.security_in_repo("+x = 1", "REVUE_Q", 10_000), "GIT DIFF :"),
            (prompts.security_standalone("x = 1", "REVUE_Q", 10_000), "CODE PRODUIT :"),
        ):
            self.assertIn(label, prompt)
            self.assertIn("REVIEW QUALITÉ PRÉALABLE :\nREVUE_Q", prompt)
            self.assertNotIn("CAHIER DES CHARGES", prompt)
            self.assertIn("VERDICT: PASS", prompt)

    def test_dev_prompts(self):
        first = prompts.dev_in_repo(DevSpecialty.PYTHON, self.SPEC)
        self.assertIn("Tu es un expert Dev Python", first)
        self.assertIn(f"CAHIER DES CHARGES :\n{self.SPEC}", first)
        self.assertNotIn("RETOURS OBLIGATOIRES", first)

        retry = prompts.dev_in_repo(DevSpecialty.PYTHON, self.SPEC, feedback="- corrige X")
        self.assertIn("RETOURS OBLIGATOIRES À CORRIGER :\n- corrige X", retry)

        standalone_retry = prompts.dev_standalone(DevSpecialty.UI, self.SPEC, previous_code="OLD", feedback="- fix")
        self.assertIn("CODE ACTUEL :\nOLD", standalone_retry)
        self.assertIn("RETOURS OBLIGATOIRES", standalone_retry)

    def test_large_inputs_are_truncated_explicitly(self):
        prompt = prompts.quality_in_repo("x" * 100_000, 5_000)
        self.assertLess(len(prompt), 7_000)
        self.assertIn("caractères omis", prompt)

    def test_spec_prompt_lists_generated_directories_to_ignore(self):
        spec = prompts.spec_in_repo("Ajoute un tri")
        for ignored in ("node_modules", ".next", "dist", "__pycache__"):
            self.assertIn(ignored, spec)

    def test_every_specialty_has_a_system_prompt(self):
        for specialty in DevSpecialty:
            self.assertTrue(prompts.specialty_system_prompt(specialty), specialty)

    def test_doc_edit_prompt_forbids_touching_code(self):
        prompt = prompts.doc_edit("+x = 1", "NOTE", 10_000)
        self.assertIn("Ne modifie AUCUN fichier de code", prompt)
        self.assertIn("GIT DIFF VALIDÉ :", prompt)
        self.assertIn("NOTE", prompt)


class TestRolePolicyForDocEdit(unittest.TestCase):
    def test_doc_edit_can_edit_files_but_has_no_bash_and_is_not_an_isolated_reviewer(self):
        policy = policy_for(Role.DOC_EDIT, allow_bash=True)
        self.assertIn("Edit", policy.tools.split(","))
        self.assertNotIn("Bash", policy.tools.split(","))
        self.assertEqual(policy.permission_mode, "acceptEdits")
        self.assertFalse(Role.DOC_EDIT.is_isolated_reviewer)
        self.assertTrue(Role.DOC.is_isolated_reviewer)


class TestPromptsVersion(unittest.TestCase):
    def test_no_leftover_placeholders(self):
        samples = [
            prompts.spec_in_repo("r"), prompts.spec_standalone("r"),
            prompts.dev_in_repo(DevSpecialty.PYTHON, "s"), prompts.dev_standalone(DevSpecialty.PYTHON, "s"),
            prompts.feedback_quality("r"), prompts.feedback_security("r"),
            prompts.doc_in_repo("d", 1000), prompts.doc_standalone("c", 1000),
        ]
        for text in samples:
            self.assertIsNone(re.search(r"\{[a-z_]+\}", text), text[:60])


if __name__ == "__main__":
    unittest.main()

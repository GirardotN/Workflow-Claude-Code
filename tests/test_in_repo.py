"""
Suite de tests unitaires pour le mode In-Repo de l'orchestrateur.
Teste l'exploration dans un dépôt Git existant multi-fichiers,
la modification chirurgicale in-situ, la revue sur git diff,
le rollback automatique sur rejet et le commit automatique.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from clients.claude_cli import ClaudeCliClient
from clients.git_client import GitClient
from clients.jev_client import JevClient
from models import DevSpecialty, WorkflowType
from orchestrator import MultiAgentOrchestrator


class TestInRepoWorkflow(unittest.TestCase):

    def setUp(self):
        # Création d'un dépôt Git temporaire multi-fichiers
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_path = Path(self.temp_dir.name)

        # Initialisation de Git
        subprocess.run(["git", "init"], cwd=self.repo_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=self.repo_path, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_path, check=True)

        # Création d'une structure multi-fichiers réaliste (dizaines de composants)
        src_dir = self.repo_path / "src" / "components"
        src_dir.mkdir(parents=True, exist_ok=True)

        # 1. Le composant cible : TabX.tsx
        self.tab_x_file = src_dir / "TabX.tsx"
        self.tab_x_content = (
            "import React from 'react';\n\n"
            "export const TabX = ({ data }) => {\n"
            "  // Fonction de tri actuelle par ordre alphabétique\n"
            "  const sortedItems = [...data].sort((a, b) => a.name.localeCompare(b.name));\n"
            "  return <div>{sortedItems.map(i => <span key={i.id}>{i.name}</span>)}</div>;\n"
            "};\n"
        )
        self.tab_x_file.write_text(self.tab_x_content, encoding="utf-8")

        # 2. D'autres fichiers pour simuler un projet existant
        (src_dir / "TabY.tsx").write_text("export const TabY = () => <div>Tab Y</div>;", encoding="utf-8")
        (src_dir / "Header.tsx").write_text("export const Header = () => <header>Nav</header>;", encoding="utf-8")
        (src_dir / "Sidebar.tsx").write_text("export const Sidebar = () => <aside>Sidebar</aside>;", encoding="utf-8")
        (self.repo_path / "package.json").write_text('{"name": "test-project", "version": "1.0.0"}', encoding="utf-8")
        (self.repo_path / "README.md").write_text("# Test Project", encoding="utf-8")

        # Commit initial
        subprocess.run(["git", "add", "-A"], cwd=self.repo_path, check=True)
        subprocess.run(["git", "commit", "-m", "chore: initial commit"], cwd=self.repo_path, check=True)

        self.git = GitClient(str(self.repo_path))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_in_repo_exploration_and_in_situ_modification(self):
        """
        Vérifie qu'en mode In-Repo :
        - L'orchestrateur cible bien le répertoire existant.
        - Le composant TabX.tsx est modifié in-situ.
        - Les autres fichiers (TabY.tsx, Header.tsx) ne sont pas touchés.
        - Le git diff est capturé et validé.
        """
        claude = ClaudeCliClient(mock_mode=True)
        jev = JevClient(mock_mode=True)

        orchestrator = MultiAgentOrchestrator(
            claude_client=claude,
            jev_client=jev,
            project_dir=str(self.repo_path),
            standalone_mode=False,
            auto_commit=False,
        )

        prompt = "Modifie la fonction de tri dans l'onglet x pour trier par date décroissante"
        report = orchestrator.run(prompt)

        self.assertTrue(report.is_success)
        self.assertTrue(report.is_in_repo)
        self.assertEqual(report.project_dir, str(self.repo_path))

        # Vérifier que TabX.tsx a bien été modifié
        new_tab_x_content = self.tab_x_file.read_text(encoding="utf-8")
        self.assertNotEqual(new_tab_x_content, self.tab_x_content)
        self.assertIn("date", new_tab_x_content)

        # Vérifier que les autres fichiers n'ont PAS été modifiés
        tab_y_content = (self.repo_path / "src" / "components" / "TabY.tsx").read_text(encoding="utf-8")
        self.assertEqual(tab_y_content, "export const TabY = () => <div>Tab Y</div>;")

        # Vérifier le git diff
        self.assertTrue(len(report.git_diff) > 0)
        self.assertIn("TabX.tsx", report.git_diff)
        self.assertIn("src/components/TabX.tsx", report.modified_files)

    def test_in_repo_auto_commit_option(self):
        """
        Vérifie que l'option auto_commit=True applique le commit directement
        dans l'historique Git du projet.
        """
        claude = ClaudeCliClient(mock_mode=True)
        jev = JevClient(mock_mode=True)

        orchestrator = MultiAgentOrchestrator(
            claude_client=claude,
            jev_client=jev,
            project_dir=str(self.repo_path),
            auto_commit=True,
        )

        prompt = "Modifie la fonction de tri dans l'onglet x"
        report = orchestrator.run(prompt)

        self.assertTrue(report.is_success)
        self.assertIsNotNone(report.commit_hash)

        # Vérifier que le dépôt Git est propre après commit
        self.assertTrue(self.git.is_working_tree_clean())

        # Vérifier que le dernier commit dans git log correspond
        head_commit = self.git.get_head_commit()
        self.assertEqual(report.commit_hash, head_commit)

    def test_in_repo_rollback_on_rejection(self):
        """
        Vérifie qu'en cas de rejet par Jev, l'orchestrateur effectue un rollback Git
        propre avant de relancer l'itération suivante.
        """
        # Création de clients contrôlés
        claude = ClaudeCliClient(mock_mode=True)

        class RejectionThenApprovalJevClient:
            def __init__(self):
                self.calls = 0

            def classify(self, context, choices, **kwargs):
                return choices[0]

            def validate(self, context, criteria):
                self.calls += 1
                # 1er appel : Rejet qualité -> doit déclencher rollback
                # 2e appel : Validation qualité -> succès
                if self.calls == 1:
                    return False
                return True

        jev_mock = RejectionThenApprovalJevClient()

        orchestrator = MultiAgentOrchestrator(
            claude_client=claude,
            jev_client=jev_mock,
            project_dir=str(self.repo_path),
            max_retries=3,
        )

        prompt = "Modifie la fonction de tri dans l'onglet x"
        report = orchestrator.run(prompt)

        self.assertTrue(report.is_success)
        self.assertEqual(report.iterations_count, 2)
        # Vérifier que les étapes de feedback ont été enregistrées
        step_names = [s.step_name for s in report.history]
        self.assertIn("DEV_IN_SITU_CYCLE_1", step_names)
        self.assertIn("CHECK_QUALITE_DIFF_CYCLE_1", step_names)
        self.assertIn("FEEDBACK_QUALITE_CYCLE_1", step_names)
        self.assertIn("DEV_IN_SITU_CYCLE_2", step_names)

    def test_in_repo_branch_isolation_and_merge(self):
        """
        Vérifie qu'avec use_branch=True et auto_merge=True, le travail
        s'effectue sur une branche dédiée puis est fusionné dans la branche d'origine.
        """
        claude = ClaudeCliClient(mock_mode=True)
        jev = JevClient(mock_mode=True)

        initial_branch = self.git.get_current_branch()

        orchestrator = MultiAgentOrchestrator(
            claude_client=claude,
            jev_client=jev,
            project_dir=str(self.repo_path),
            use_branch=True,
            auto_commit=True,
            auto_merge=True,
        )

        prompt = "Modifie la fonction de tri dans l'onglet x"
        report = orchestrator.run(prompt)

        self.assertTrue(report.is_success)
        self.assertEqual(report.original_branch, initial_branch)
        # Après auto-merge, on est revenu sur la branche initiale
        current_branch = self.git.get_current_branch()
        self.assertEqual(current_branch, initial_branch)

    def test_in_repo_test_feedback_loop(self):
        """
        Vérifie que si la suite de tests échoue après édition, le feedback
        d'erreur est réinjecté à Claude pour auto-correction avant la revue Jev.
        """
        claude = ClaudeCliClient(mock_mode=True)
        jev = JevClient(mock_mode=True)

        from clients.test_runner import TestResult, TestRunner

        class MockFailingThenPassingTestRunner(TestRunner):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def run_tests(self, project_dir, custom_cmd=None):
                self.calls += 1
                if self.calls == 1:
                    return TestResult(
                        passed=False,
                        command="npm test",
                        output="FAIL: TypeError: Cannot read property 'date' of undefined",
                        duration_seconds=0.1,
                        returncode=1,
                    )
                return TestResult(
                    passed=True,
                    command="npm test",
                    output="PASS: All 12 tests passed",
                    duration_seconds=0.1,
                    returncode=0,
                )

        runner = MockFailingThenPassingTestRunner()

        orchestrator = MultiAgentOrchestrator(
            claude_client=claude,
            jev_client=jev,
            test_runner=runner,
            project_dir=str(self.repo_path),
            run_tests=True,
            max_retries=3,
        )

        prompt = "Modifie la fonction de tri dans l'onglet x"
        report = orchestrator.run(prompt)

        self.assertTrue(report.is_success)
        self.assertTrue(report.tests_passed)
        step_names = [s.step_name for s in report.history]
        self.assertIn("TESTS_FAILED_CYCLE_1", step_names)
        self.assertIn("DEV_IN_SITU_CYCLE_2", step_names)
        self.assertIn("TESTS_PASSED_CYCLE_2", step_names)

    def test_in_repo_untracked_new_file_captured_in_diff(self):
        """
        Vérifie que la création d'un tout nouveau fichier (non suivi/untracked)
        est immédiatement capturée dans git diff grâce à git add -N.
        """
        new_file = self.repo_path / "src" / "components" / "NewBrandComponent.tsx"
        new_file.write_text("export const NewBrandComponent = () => <div>Brand New</div>;\n", encoding="utf-8")

        # À ce stade, le fichier n'est pas indexé (untracked)
        diff = self.git.get_diff()

        # Le diff doit impérativement contenir le nouveau fichier et son contenu
        self.assertIn("NewBrandComponent.tsx", diff)
        self.assertIn("Brand New", diff)


if __name__ == "__main__":
    unittest.main()



"""
Tests unitaires pour le composant TestRunner (détection et exécution des tests).
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from workflow_claude.clients.test_runner import TestRunner


class TestTestRunner(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_path = Path(self.temp_dir.name)
        self.runner = TestRunner(timeout_seconds=10)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_detect_nodejs_tests(self):
        """Vérifie la détection d'un projet Node.js avec script test."""
        pkg_json = self.project_path / "package.json"
        pkg_json.write_text(json.dumps({
            "name": "my-node-app",
            "scripts": {
                "test": "jest --coverage"
            }
        }), encoding="utf-8")

        cmd = self.runner.detect_test_command(self.project_path)
        self.assertIsNotNone(cmd)
        self.assertIn("test", cmd)

    def test_ignore_dummy_nodejs_test(self):
        """Ignore le script de test par défaut généré par npm init."""
        pkg_json = self.project_path / "package.json"
        pkg_json.write_text(json.dumps({
            "name": "my-node-app",
            "scripts": {
                "test": 'echo "Error: no test specified" && exit 1'
            }
        }), encoding="utf-8")

        cmd = self.runner.detect_test_command(self.project_path)
        self.assertIsNone(cmd)

    def test_detect_python_tests(self):
        """Vérifie la détection d'un projet Python avec dossier tests/."""
        tests_dir = self.project_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_example.py").write_text("import unittest\n", encoding="utf-8")

        cmd = self.runner.detect_test_command(self.project_path)
        self.assertIsNotNone(cmd)
        self.assertTrue("pytest" in cmd or "unittest" in cmd)

    def test_run_tests_success(self):
        """Vérifie l'exécution réussie d'une commande de test."""
        res = self.runner.run_tests(self.project_path, custom_cmd=[sys.executable, "-c", "exit(0)"])
        self.assertIsNotNone(res)
        self.assertTrue(res.passed)
        self.assertEqual(res.returncode, 0)

    def test_run_tests_failure(self):
        """Vérifie la capture propre d'une commande de test en échec."""
        res = self.runner.run_tests(
            self.project_path,
            custom_cmd=[sys.executable, "-c", "import sys; sys.stderr.write('AssertionError: expected 1 got 2'); sys.exit(1)"],
        )
        self.assertIsNotNone(res)
        self.assertFalse(res.passed)
        self.assertEqual(res.returncode, 1)
        self.assertIn("AssertionError", res.output)

    def test_detect_python_singular_test_dir(self):
        """Vérifie la détection d'un projet Python avec dossier singulier test/."""
        test_dir = self.project_path / "test"
        test_dir.mkdir()
        (test_dir / "test_sample.py").write_text("def test_ok(): pass\n", encoding="utf-8")

        cmd = self.runner.detect_test_command(self.project_path)
        self.assertIsNotNone(cmd)
        if "unittest" in cmd:
            self.assertIn("test", cmd)
            self.assertNotIn("tests", cmd)


if __name__ == "__main__":
    unittest.main()

"""
Module d'exécution et de détection automatique des tests pour le projet cible.
Sert d'oracle de vérité dans la boucle de feedback multi-agents.
"""

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("test_runner")


@dataclass
class TestResult:
    """Résultat d'une exécution de la suite de tests."""
    passed: bool
    command: str
    output: str
    duration_seconds: float
    returncode: int


class TestRunner:
    """
    Détecte et exécute la suite de tests du projet hôte (pytest, npm test, cargo, go).
    """

    def __init__(self, timeout_seconds: int = 120):
        self.timeout_seconds = timeout_seconds

    def detect_test_command(self, project_dir: Path) -> Optional[List[str]]:
        """
        Analyse l'arborescence du projet pour détecter le framework de test applicable.
        Retourne la commande sous forme de liste d'arguments, ou None si aucun test détecté.
        """
        # 1. Projet Node.js / TypeScript (package.json)
        pkg_json = project_dir / "package.json"
        if pkg_json.is_file():
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
                scripts = data.get("scripts", {})
                test_script = scripts.get("test", "")
                # Ignore les placeholders npm par défaut qui échouent toujours
                if test_script and "no test specified" not in test_script:
                    npm_bin = shutil.which("npm") or "npm"
                    return [npm_bin, "test"]
            except Exception as e:
                logger.debug(f"Impossible de parser package.json : {e}")

        # 2. Projet Python (pytest / unittest)
        has_python_tests = False
        detected_test_folder = "tests"
        for test_folder in ("tests", "test"):
            tf = project_dir / test_folder
            if tf.is_dir() and any(tf.glob("*.py")):
                has_python_tests = True
                detected_test_folder = test_folder
                break

        if has_python_tests or (project_dir / "pytest.ini").is_file() or (project_dir / "setup.cfg").is_file():
            if shutil.which("pytest"):
                return ["pytest"]
            python_bin = shutil.which("python3") or shutil.which("python") or "python3"
            return [python_bin, "-m", "unittest", "discover", "-s", detected_test_folder]

        # 3. Projet Rust (Cargo.toml)
        if (project_dir / "Cargo.toml").is_file() and shutil.which("cargo"):
            return ["cargo", "test"]

        # 4. Projet Go (go.mod)
        if (project_dir / "go.mod").is_file() and shutil.which("go"):
            return ["go", "test", "./..."]

        return None

    def run_tests(
        self,
        project_dir: Path,
        custom_cmd: Optional[str] = None,
    ) -> Optional[TestResult]:
        """
        Exécute la commande de test dans le répertoire du projet.
        Retourne un objet TestResult, ou None si aucun test n'a pu être exécuté.
        """
        if custom_cmd:
            import shlex
            cmd = shlex.split(custom_cmd)
        else:
            cmd = self.detect_test_command(project_dir)


        if not cmd:
            logger.info("Aucune suite de tests automatisée détectée dans le projet.")
            return None

        cmd_str = " ".join(cmd)
        logger.info(f"Lancement de la suite de tests du projet : {cmd_str}...")

        start_time = time.perf_counter()
        try:
            exec_cmd = cmd
            if os.name == "nt" and cmd and cmd[0].lower().endswith((".cmd", ".bat")):
                comspec = os.environ.get("COMSPEC", "cmd.exe")
                exec_cmd = [comspec, "/d", "/c"] + cmd

            proc = subprocess.run(
                exec_cmd,
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                shell=False,
            )
            duration = time.perf_counter() - start_time
            passed = (proc.returncode == 0)
            combined_output = (proc.stdout + "\n" + proc.stderr).strip()

            # Tronque la sortie si elle est démesurément volumineuse pour le prompt LLM
            if len(combined_output) > 8000:
                combined_output = combined_output[:4000] + "\n\n... [TRONQUÉ] ...\n\n" + combined_output[-4000:]

            status_str = "PASSÉ" if passed else "ÉCHOUÉ"
            logger.info(f"Résultat des tests ({status_str}) en {duration:.2f}s (code {proc.returncode})")
            return TestResult(
                passed=passed,
                command=cmd_str,
                output=combined_output,
                duration_seconds=duration,
                returncode=proc.returncode,
            )

        except subprocess.TimeoutExpired:
            duration = time.perf_counter() - start_time
            logger.warning(f"Timeout ({self.timeout_seconds}s) dépassé lors de l'exécution des tests.")
            return TestResult(
                passed=False,
                command=cmd_str,
                output=f"TimeoutExpired : les tests ont dépassé le délai imparti de {self.timeout_seconds} secondes.",
                duration_seconds=duration,
                returncode=-1,
            )
        except Exception as e:
            duration = time.perf_counter() - start_time
            logger.error(f"Erreur d'exécution de la commande de tests '{cmd_str}' : {e}")
            return TestResult(
                passed=False,
                command=cmd_str,
                output=f"Exception lors de l'exécution des tests : {e}",
                duration_seconds=duration,
                returncode=-2,
            )

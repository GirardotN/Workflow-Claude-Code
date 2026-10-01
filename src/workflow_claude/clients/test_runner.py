"""
Détection et exécution de la suite de tests du projet cible : l'« oracle de vérité » de la boucle de feedback.

Ordre de résolution de la commande de test :
  1. `custom_cmd` passé à `run_tests` / commande fournie au constructeur (`--test-cmd`, `TEST_COMMAND`) ;
  2. fichier `.workflow.toml` du projet : `[tests]  command = "..."  timeout = 300` ;
  3. détection automatique (Node : npm/pnpm/yarn/bun selon le lockfile ; Python : pytest ou unittest avec le venv
     du projet ; .NET ; Maven ; Gradle ; Rust ; Go).

Les tests tournent sans terminal interactif (stdin fermé, `CI=true`, sans couleurs) ; au timeout, l'arbre de
processus entier est tué. Les identifiants des tests en échec sont extraits de la sortie (pytest, unittest, jest,
vitest, go, cargo, dotnet) afin de distinguer une vraie régression d'un échec préexistant.
"""

import json
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set, Union

from ..config import MAX_TEST_OUTPUT_CHARS, TEST_COMMAND, TEST_TIMEOUT_SECONDS
from ..jev_context import truncate_middle
from ..text_utils import normalize_test_output

logger = logging.getLogger("test_runner")

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_VENV_NAMES = (".venv", "venv", "env")


@dataclass
class TestResult:
    """Résultat d'une exécution de la suite de tests."""
    __test__ = False  # n'est pas une classe de tests pour pytest/unittest

    passed: bool
    command: str
    output: str
    duration_seconds: float
    returncode: int
    failed_tests: List[str] = field(default_factory=list)  # identifiants extraits de la sortie (peut être vide)
    timed_out: bool = False


# ---------------------------------------------------------------------------
# Analyse de la sortie
# ---------------------------------------------------------------------------
_FAILED_PATTERNS = [
    re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE),                       # pytest : FAILED tests/x.py::test_a - msg
    re.compile(r"^(?:FAIL|ERROR): (.+?)\s*$", re.MULTILINE),                    # unittest : FAIL: test_a (mod.Class.test_a)
    re.compile(r"^\s*●\s+(?!Console\b|Test suite failed)(.+?)\s*$", re.MULTILINE),  # jest : ● Suite › test
    re.compile(r"^\s*FAIL\s{2,}(\S.*?)\s*$", re.MULTILINE),                     # vitest : FAIL  file > suite > test
    re.compile(r"^\s*--- FAIL: (\S+)", re.MULTILINE),                           # go : --- FAIL: TestName
    re.compile(r"^test (\S+) \.\.\. FAILED", re.MULTILINE),                     # cargo
    re.compile(r"^\s*Failed (\S+)", re.MULTILINE),                              # dotnet : Failed Ns.Class.Method [12 ms]
]


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def parse_failed_tests(output: str) -> List[str]:
    """Identifiants des tests en échec trouvés dans la sortie (trié, sans doublon ; vide si non reconnu)."""
    found: Set[str] = set()
    for pattern in _FAILED_PATTERNS:
        for match in pattern.finditer(output):
            found.add(match.group(1).strip())
    return sorted(found)


def is_regression(baseline: Optional[TestResult], result: TestResult) -> bool:
    """
    Vrai si `result` contient une NOUVELLE défaillance par rapport à la baseline (état initial du projet).

    - tests réussis                                  -> jamais une régression ;
    - baseline verte (ou absente) et tests en échec  -> régression ;
    - baseline en échec : on compare les ENSEMBLES de tests en échec (insensible aux temps, adresses mémoire,
      chemins temporaires et à l'ordre) : régression seulement si un test échoue qui n'échouait pas avant ;
      si les identifiants ne sont pas lisibles, repli sur la comparaison de la sortie normalisée.
    """
    if result.passed:
        return False
    if baseline is None or baseline.passed:
        return True

    before, now = set(baseline.failed_tests), set(result.failed_tests)
    if before and now:
        return bool(now - before)
    return normalize_test_output(result.output) != normalize_test_output(baseline.output)


# ---------------------------------------------------------------------------
# Configuration par projet
# ---------------------------------------------------------------------------
def read_project_config(project_dir: Path) -> dict:
    """Lit la section [tests] de `.workflow.toml` : {"command": "...", "timeout": 300} (clés facultatives)."""
    path = project_dir / ".workflow.toml"
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        import tomllib  # Python 3.11+
        section = tomllib.loads(text).get("tests", {})
        return section if isinstance(section, dict) else {}
    except ImportError:
        return _parse_tests_section_fallback(text)
    except Exception as e:
        logger.warning(f".workflow.toml illisible ({e}) : ignoré.")
        return {}


def _parse_tests_section_fallback(text: str) -> dict:
    """Lecteur minimal de `[tests]` pour Python 3.10 (pas de tomllib) : `command = "..."` et `timeout = N`."""
    section, config = None, {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line.strip("[]").strip()
            continue
        if section != "tests" or "=" not in line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key == "command":
            config["command"] = value.strip("'\"")
        elif key == "timeout" and value.isdigit():
            config["timeout"] = int(value)
    return config


# ---------------------------------------------------------------------------
# Détection de l'environnement
# ---------------------------------------------------------------------------
def _is_windows_store_stub(path: Optional[str]) -> bool:
    """`python3.exe` du dossier WindowsApps n'est qu'un raccourci vers le Microsoft Store (code 9009)."""
    return bool(path) and "windowsapps" in path.lower()


def find_python(project_dir: Path) -> str:
    """Interpréteur Python à utiliser pour les tests : venv du projet, puis python du PATH, puis celui-ci."""
    for name in _VENV_NAMES:
        for rel in ("Scripts/python.exe", "bin/python"):
            candidate = project_dir / name / rel
            if candidate.is_file():
                return str(candidate)
    for exe in ("python3", "python"):
        found = shutil.which(exe)
        if found and not _is_windows_store_stub(found):
            return found
    return sys.executable


def python_has_module(python: str, module: str, timeout: int = 20) -> bool:
    try:
        proc = subprocess.run([python, "-c", f"import {module}"], capture_output=True, timeout=timeout)
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Tue le processus ET ses descendants (npm/pytest/dotnet lancent des enfants qui survivraient sinon)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=20)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


class TestRunner:
    """Détecte et exécute la suite de tests du projet hôte."""
    __test__ = False  # n'est pas une classe de tests pour pytest/unittest

    def __init__(
        self,
        timeout_seconds: int = TEST_TIMEOUT_SECONDS,
        test_command: Optional[Union[str, List[str]]] = TEST_COMMAND or None,
        max_output_chars: int = MAX_TEST_OUTPUT_CHARS,
    ):
        self.timeout_seconds = timeout_seconds
        self.test_command = test_command
        self.max_output_chars = max_output_chars

    # ------------------------------------------------------------------
    def detect_test_command(self, project_dir: Path) -> Optional[List[str]]:
        """
        Analyse l'arborescence du projet pour détecter le framework de test applicable.
        Retourne la commande sous forme de liste d'arguments, ou None si aucun test détecté.
        """
        # 1. Node.js / TypeScript (package.json) : gestionnaire de paquets choisi d'après le lockfile
        pkg_json = project_dir / "package.json"
        if pkg_json.is_file():
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
                test_script = data.get("scripts", {}).get("test", "")
                # Ignore les placeholders npm par défaut qui échouent toujours
                if test_script and "no test specified" not in test_script:
                    return self._node_command(project_dir)
            except Exception as e:
                logger.debug(f"Impossible de parser package.json : {e}")

        # 2. Python
        python_command = self._python_command(project_dir)
        if python_command:
            return python_command

        # 3. .NET
        if self._has_dotnet_project(project_dir) and shutil.which("dotnet"):
            return ["dotnet", "test", "--nologo"]

        # 4. Java : Maven puis Gradle (wrapper du projet en priorité)
        if (project_dir / "pom.xml").is_file():
            wrapper = project_dir / ("mvnw.cmd" if os.name == "nt" else "mvnw")
            if wrapper.is_file():
                return [str(wrapper), "-q", "test"]
            if shutil.which("mvn"):
                return ["mvn", "-q", "test"]
        if (project_dir / "build.gradle").is_file() or (project_dir / "build.gradle.kts").is_file():
            wrapper = project_dir / ("gradlew.bat" if os.name == "nt" else "gradlew")
            if wrapper.is_file():
                return [str(wrapper), "test"]
            if shutil.which("gradle"):
                return ["gradle", "test"]

        # 5. Rust, 6. Go
        if (project_dir / "Cargo.toml").is_file() and shutil.which("cargo"):
            return ["cargo", "test"]
        if (project_dir / "go.mod").is_file() and shutil.which("go"):
            return ["go", "test", "./..."]

        return None

    @staticmethod
    def _node_command(project_dir: Path) -> List[str]:
        if (project_dir / "pnpm-lock.yaml").is_file():
            manager, args = "pnpm", ["test"]
        elif (project_dir / "yarn.lock").is_file():
            manager, args = "yarn", ["test"]
        elif (project_dir / "bun.lockb").is_file() or (project_dir / "bun.lock").is_file():
            manager, args = "bun", ["run", "test"]
        else:
            manager, args = "npm", ["test"]
        return [shutil.which(manager) or manager] + args

    def _python_command(self, project_dir: Path) -> Optional[List[str]]:
        test_folder = None
        for folder in ("tests", "test"):
            path = project_dir / folder
            if path.is_dir() and any(path.glob("*.py")):
                test_folder = folder
                break
        has_pytest_config = any((project_dir / name).is_file() for name in ("pytest.ini", "conftest.py"))
        has_setup = (project_dir / "setup.cfg").is_file()
        pyproject = project_dir / "pyproject.toml"
        has_pyproject_pytest = pyproject.is_file() and "[tool.pytest" in pyproject.read_text(encoding="utf-8", errors="replace")
        if not (test_folder or has_pytest_config or has_setup or has_pyproject_pytest):
            return None

        python = find_python(project_dir)
        if python_has_module(python, "pytest"):
            return [python, "-m", "pytest"]
        return [python, "-m", "unittest", "discover", "-s", test_folder or "tests"]

    @staticmethod
    def _has_dotnet_project(project_dir: Path) -> bool:
        for pattern in ("*.sln", "*.csproj", "*/*.csproj"):
            if any(project_dir.glob(pattern)):
                return True
        return False

    # ------------------------------------------------------------------
    def resolve_command(self, project_dir: Path, custom_cmd: Optional[Union[str, List[str]]] = None) -> Optional[List[str]]:
        """Commande effective : argument > constructeur/--test-cmd > .workflow.toml > détection automatique."""
        for source in (custom_cmd, self.test_command, read_project_config(project_dir).get("command")):
            if source:
                if isinstance(source, str):
                    return shlex.split(source, posix=(os.name != "nt"))
                return list(source)
        return self.detect_test_command(project_dir)

    def run_tests(
        self,
        project_dir: Path,
        custom_cmd: Optional[Union[str, List[str]]] = None,
    ) -> Optional[TestResult]:
        """
        Exécute la commande de test dans le répertoire du projet.
        `custom_cmd` peut être une liste d'arguments (recommandé, portable) ou une chaîne
        découpée avec shlex (mode POSIX désactivé sous Windows pour préserver les backslashes).
        Retourne un objet TestResult, ou None si aucun test n'a pu être exécuté.
        """
        cmd = self.resolve_command(project_dir, custom_cmd)
        if not cmd:
            logger.info("Aucune suite de tests automatisée détectée dans le projet.")
            return None

        timeout = read_project_config(project_dir).get("timeout")
        timeout = int(timeout) if isinstance(timeout, int) and timeout > 0 else self.timeout_seconds

        cmd_str = " ".join(cmd)
        logger.info(f"Lancement de la suite de tests du projet : {cmd_str}...")
        start_time = time.perf_counter()

        exec_cmd = cmd
        if os.name == "nt" and cmd[0].lower().endswith((".cmd", ".bat")):
            exec_cmd = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c"] + cmd

        env = {**os.environ, "CI": "true", "NO_COLOR": "1", "FORCE_COLOR": "0", "PYTHONDONTWRITEBYTECODE": "1"}
        popen_kwargs = {"start_new_session": True} if os.name != "nt" else {}
        try:
            proc = subprocess.Popen(
                exec_cmd, cwd=str(project_dir), env=env, shell=False,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **popen_kwargs,
            )
        except Exception as e:
            logger.error(f"Erreur d'exécution de la commande de tests '{cmd_str}' : {e}")
            return TestResult(False, cmd_str, f"Exception lors de l'exécution des tests : {e}",
                              time.perf_counter() - start_time, -2)

        timed_out = False
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process_tree(proc)
            try:
                stdout, stderr = proc.communicate(timeout=15)
            except Exception:
                stdout, stderr = b"", b""

        duration = time.perf_counter() - start_time
        if timed_out:
            logger.warning(f"Timeout ({timeout}s) dépassé lors de l'exécution des tests : arbre de processus arrêté.")
            output = f"TimeoutExpired : les tests ont dépassé le délai imparti de {timeout} secondes."
            return TestResult(False, cmd_str, output, duration, -1, timed_out=True)

        text = strip_ansi((stdout.decode("utf-8", errors="replace") + "\n" + stderr.decode("utf-8", errors="replace")).strip())
        failed = parse_failed_tests(text)  # avant troncature : la liste des échecs doit rester complète
        passed = proc.returncode == 0
        # Tronque la sortie si elle est démesurément volumineuse pour le prompt LLM
        output = truncate_middle(text, self.max_output_chars)

        logger.info(f"Résultat des tests ({'PASSÉ' if passed else 'ÉCHOUÉ'}) en {duration:.2f}s (code {proc.returncode})")
        return TestResult(passed, cmd_str, output, duration, proc.returncode, failed_tests=failed)

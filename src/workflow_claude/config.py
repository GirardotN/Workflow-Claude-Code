"""
Configuration globale pour l'orchestrateur multi-agents Claude & TypeSafe Jev.
"""

import json
import os
import shutil
from pathlib import Path
from typing import Any, List

USER_CONFIG_DIR = Path.home() / ".config" / "workflow-claude"
_TRUE_VALUES = ("1", "true", "yes", "on")
_FALSE_VALUES = ("0", "false", "no", "off", "")


def _load_env_file(env_path: Path) -> None:
    """Charge un fichier .env sans écraser les variables déjà définies dans l'environnement."""
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)
        return
    except ImportError:
        pass
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    key, value = key.strip(), value.strip().strip("'\"")
                    if key and key not in os.environ:
                        os.environ[key] = value
    except OSError:
        pass


def _env_search_paths() -> List[Path]:
    """
    Emplacements des fichiers .env, par priorité décroissante :
    1. le répertoire courant (ce que l'utilisateur voit),
    2. le répertoire de configuration utilisateur (~/.config/workflow-claude/.env).
    Les variables déjà présentes dans l'environnement du processus restent prioritaires.
    """
    return [Path.cwd() / ".env", USER_CONFIG_DIR / ".env"]


for _candidate in _env_search_paths():
    if _candidate.is_file():
        _load_env_file(_candidate)


def env_bool(name: str, default: bool) -> bool:
    """Lit une variable d'environnement booléenne (1/true/yes/on ↔ 0/false/no/off)."""
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    return default


def _coerce_bool(value: Any, default: bool) -> bool:
    """Convertit une valeur de config.json en booléen strict (évite bool('false') == True)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_VALUES:
            return True
        if lowered in _FALSE_VALUES:
            return False
    return default


def _find_claude_binary() -> str:
    """Détecte l'emplacement du binaire Claude Code CLI."""
    # 1. Variable d'environnement explicite
    if os.getenv("CLAUDE_BIN"):
        return os.getenv("CLAUDE_BIN")

    # 2. Dans le PATH système
    which_path = shutil.which("claude")
    if which_path:
        return which_path

    # 3. Emplacements standards connus (Linux, macOS, Windows)
    # Détection dynamique dans ~/.config/Claude/claude-code/<version>/claude
    claude_code_dir = Path.home() / ".config" / "Claude" / "claude-code"
    if claude_code_dir.is_dir():
        try:
            version_dirs = sorted([d for d in claude_code_dir.iterdir() if d.is_dir()], reverse=True)
            for vdir in version_dirs:
                bin_file = vdir / "claude"
                if bin_file.is_file() and (os.name == "nt" or os.access(bin_file, os.X_OK)):
                    return str(bin_file)
        except Exception:
            pass

    appdata = os.getenv("APPDATA", "")
    localappdata = os.getenv("LOCALAPPDATA", "")
    program_files = os.getenv("ProgramFiles", "C:\\Program Files")

    candidates = [
        # Linux / macOS
        Path.home() / ".local" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
        Path.home() / ".npm-global" / "bin" / "claude",
        # Windows (npm global, AppData, Program Files)
        Path(appdata) / "npm" / "claude.cmd" if appdata else None,
        Path.home() / "AppData" / "Roaming" / "npm" / "claude.cmd",
        Path(localappdata) / "Programs" / "Claude" / "claude.exe" if localappdata else None,
        Path(program_files) / "nodejs" / "claude.cmd",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            # Sur Windows os.access X_OK n'est pas fiable sur les fichiers .cmd
            if os.name == "nt" or os.access(candidate, os.X_OK):
                return str(candidate)

    return "claude"  # Par défaut, se reposer sur PATH


# Configuration Claude CLI
CLAUDE_BIN_PATH = _find_claude_binary()
# Délai par défaut (revues, feedback, doc) ; l'exploration et surtout le développement sont plus longs.
CLAUDE_TIMEOUT_SECONDS = int(os.getenv("CLAUDE_TIMEOUT_SECONDS", "180"))
CLAUDE_TIMEOUT_SPEC_SECONDS = int(os.getenv("CLAUDE_TIMEOUT_SPEC_SECONDS", "300"))
CLAUDE_TIMEOUT_DEV_SECONDS = int(os.getenv("CLAUDE_TIMEOUT_DEV_SECONDS", "900"))
# Nombre de nouvelles tentatives sur erreur transitoire (serveur surchargé, 5xx)
CLAUDE_MAX_RETRIES = int(os.getenv("CLAUDE_MAX_RETRIES", "2"))
# Par défaut, les clés/fournisseurs payants de l'environnement sont retirés du sous-processus Claude
# afin de ne consommer que l'abonnement (« zéro crédit API »). ALLOW_API_KEY=1 lève cette protection.
ALLOW_API_KEY = env_bool("ALLOW_API_KEY", False)

# Configuration TypeSafe Jev API
TYPESAFE_API_KEY = os.getenv("TYPESAFE_API_KEY", "")
# Endpoint officiel System One (TypeSafe AI) avec fallback sur decide
TYPESAFE_API_URL = os.getenv("TYPESAFE_API_URL", "https://api.typesafe.ai/v1/systemone")
TYPESAFE_FALLBACK_URL = os.getenv("TYPESAFE_FALLBACK_URL", "https://api.typesafe.ai/v1/decide")
TYPESAFE_TIMEOUT_SECONDS = float(os.getenv("TYPESAFE_TIMEOUT_SECONDS", "30.0"))

# Limite de rétablissement / boucles de feedback
MAX_RETRIES = int(os.getenv("MAX_WORKFLOW_RETRIES", "4"))

# Mode simulation / mock si aucune clé ou si demandé
MOCK_SERVICES = env_bool("MOCK_SERVICES", False)

# Modèles Claude autorisés par la matrice de décision
MODEL_SONNET = "sonnet"
MODEL_OPUS = "opus"
MODEL_HAIKU = "haiku"


def load_user_config() -> dict:
    """Charge les préférences utilisateur depuis ~/.config/workflow-claude/config.json si présent."""
    cfg_path = USER_CONFIG_DIR / "config.json"
    if cfg_path.is_file():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if isinstance(data, dict):
            return data
    return {}


_user_cfg = load_user_config()

# Sécurité & Isolation par défaut (config.json > variable d'environnement > valeur par défaut)
DEFAULT_ALLOW_BASH = _coerce_bool(_user_cfg.get("allow_bash"), env_bool("ALLOW_BASH", False))
DEFAULT_RUN_TESTS = _coerce_bool(_user_cfg.get("run_tests"), env_bool("RUN_TESTS", True))
DEFAULT_USE_BRANCH = _coerce_bool(_user_cfg.get("use_branch"), env_bool("USE_BRANCH", True))



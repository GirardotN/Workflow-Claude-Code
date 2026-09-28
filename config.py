"""
Configuration globale pour l'orchestrateur multi-agents Claude & TypeSafe Jev.
"""

import os
import shutil
from pathlib import Path

# Chargement automatique des variables d'environnement (.env)
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.is_file():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path)
    except ImportError:
        try:
            with open(_env_path, encoding="utf-8") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if _line and not _line.startswith("#") and "=" in _line:
                        _k, _v = _line.split("=", 1)
                        _k, _v = _k.strip(), _v.strip().strip("'\"")
                        if _k and _k not in os.environ:
                            os.environ[_k] = _v
        except Exception:
            pass


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
CLAUDE_TIMEOUT_SECONDS = int(os.getenv("CLAUDE_TIMEOUT_SECONDS", "180"))

# Configuration TypeSafe Jev API
TYPESAFE_API_KEY = os.getenv("TYPESAFE_API_KEY", "")
# Endpoint officiel System One (TypeSafe AI) avec fallback sur decide
TYPESAFE_API_URL = os.getenv("TYPESAFE_API_URL", "https://api.typesafe.ai/v1/systemone")
TYPESAFE_FALLBACK_URL = os.getenv("TYPESAFE_FALLBACK_URL", "https://api.typesafe.ai/v1/decide")
TYPESAFE_TIMEOUT_SECONDS = float(os.getenv("TYPESAFE_TIMEOUT_SECONDS", "30.0"))

# Limite de rétablissement / boucles de feedback
MAX_RETRIES = int(os.getenv("MAX_WORKFLOW_RETRIES", "4"))

# Mode simulation / mock si aucune clé ou si demandé
MOCK_SERVICES = os.getenv("MOCK_SERVICES", "0").lower() in ("1", "true", "yes")

# Modèles Claude autorisés par la matrice de décision
MODEL_SONNET = "sonnet"
MODEL_OPUS = "opus"
MODEL_HAIKU = "haiku"

# Répertoire cible du projet à inspecter / modifier
DEFAULT_PROJECT_DIR = os.getenv("PROJECT_DIR", ".")


def load_user_config() -> dict:
    """Charge les préférences utilisateur depuis ~/.config/workflow-claude/config.json si présent."""
    cfg_path = Path.home() / ".config" / "workflow-claude" / "config.json"
    if cfg_path.is_file():
        try:
            import json
            return json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


_user_cfg = load_user_config()

# Sécurité & Isolation par défaut
DEFAULT_ALLOW_BASH = bool(_user_cfg.get("allow_bash", os.getenv("ALLOW_BASH", "0").lower() in ("1", "true", "yes")))
DEFAULT_RUN_TESTS = bool(_user_cfg.get("run_tests", os.getenv("RUN_TESTS", "1").lower() in ("1", "true", "yes")))
DEFAULT_USE_BRANCH = bool(_user_cfg.get("use_branch", os.getenv("USE_BRANCH", "1").lower() in ("1", "true", "yes")))
DEFAULT_ALLOW_DIRTY = bool(_user_cfg.get("allow_dirty", os.getenv("ALLOW_DIRTY", "0").lower() in ("1", "true", "yes")))



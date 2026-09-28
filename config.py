"""
Configuration globale pour l'orchestrateur multi-agents Claude & TypeSafe Jev.
"""

import os
import shutil
from pathlib import Path


def _find_claude_binary() -> str:
    """Détecte l'emplacement du binaire Claude Code CLI."""
    # 1. Variable d'environnement explicite
    if os.getenv("CLAUDE_BIN"):
        return os.getenv("CLAUDE_BIN")

    # 2. Dans le PATH système
    which_path = shutil.which("claude")
    if which_path:
        return which_path

    # 3. Emplacements standards connus (Desktop app, global npm, user local)
    candidates = [
        Path.home() / ".config" / "Claude" / "claude-code" / "2.1.260" / "claude",
        Path.home() / ".local" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
        Path.home() / ".npm-global" / "bin" / "claude",
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
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

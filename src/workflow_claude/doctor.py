"""
Diagnostic d'environnement (`workflow --doctor`) : vérifie, sans consommer de quota Claude,
que tout ce dont l'orchestrateur dépend est présent et utilisable. Seul appel réseau : un ping
minimal (quelques dizaines de tokens) vers TypeSafe pour valider la clé.
"""

import shutil
import subprocess
import sys
from typing import Callable, List, Optional, Tuple

from .clients.claude_cli import ClaudeCliClient, detected_billing_env
from .clients.jev_client import JevApiError, JevAuthError, JevClient, JevConfigError
from .config import CLAUDE_BIN_PATH

OK, WARN, FAIL = "ok", "warn", "fail"
_ICONS = {OK: "✅", WARN: "⚠️ ", FAIL: "❌"}

# Options du CLI Claude dont dépend le client (absentes = incompatibilité bloquante)
REQUIRED_FLAGS = ["--print", "--model", "--output-format", "--tools", "--permission-mode", "--no-session-persistence"]
OPTIONAL_FLAGS = ["--allowedTools", "--disallowedTools", "--append-system-prompt", "--system-prompt"]

Check = Tuple[str, str, str]  # (niveau, libellé, détail)


def _git_checks() -> List[Check]:
    if not shutil.which("git"):
        return [(FAIL, "Git", "binaire `git` introuvable dans le PATH")]
    checks: List[Check] = []
    version = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip()
    checks.append((OK, "Git", version))
    ident = subprocess.run(["git", "var", "GIT_COMMITTER_IDENT"], capture_output=True, text=True)
    if ident.returncode == 0:
        checks.append((OK, "Identité Git", ident.stdout.strip().rsplit(" ", 2)[0]))
    else:
        checks.append((
            WARN,
            "Identité Git",
            'absente : `git config --global user.name "Nom"` et `git config --global user.email "mail"` '
            "(nécessaire pour committer)",
        ))
    return checks


def _claude_checks(client: ClaudeCliClient, allow_api_key: bool) -> List[Check]:
    binary = client.binary_path
    if not client.binary_found(binary):
        return [(FAIL, "Claude Code CLI", f"introuvable ('{binary}') : `npm install -g @anthropic-ai/claude-code` "
                                           "ou définissez CLAUDE_BIN")]
    checks: List[Check] = []
    version = client.version()
    if version is None:
        return [(FAIL, "Claude Code CLI", f"'{binary}' ne répond pas à `--version`")]
    checks.append((OK, "Claude Code CLI", f"{version} ({binary})"))

    flags = client.supported_flags(REQUIRED_FLAGS + OPTIONAL_FLAGS)
    missing_required = [f for f in REQUIRED_FLAGS if not flags.get(f)]
    missing_optional = [f for f in OPTIONAL_FLAGS if not flags.get(f)]
    if missing_required:
        checks.append((FAIL, "Options du CLI", f"absentes : {', '.join(missing_required)} (mettez Claude Code à jour)"))
    else:
        checks.append((OK, "Options du CLI", "toutes les options requises sont disponibles"))
    if missing_optional:
        checks.append((WARN, "Options du CLI (facultatives)", f"absentes : {', '.join(missing_optional)}"))

    status = client.auth_status()
    if status is None:
        checks.append((WARN, "Session Claude", "état d'authentification illisible (`claude auth status`)"))
    elif status.get("loggedIn"):
        checks.append((OK, "Session Claude", f"connecté ({status.get('authMethod', 'méthode inconnue')})"))
    else:
        checks.append((FAIL, "Session Claude", "non connecté : exécutez `claude auth login`"))

    billing = detected_billing_env()
    if billing and allow_api_key:
        checks.append((WARN, "Facturation à l'usage", f"{', '.join(billing)} présent(s) ET --allow-api-key actif : "
                                                      "les appels Claude peuvent consommer des crédits API"))
    elif billing:
        checks.append((OK, "Facturation à l'usage", f"{', '.join(billing)} présent(s) dans votre environnement : "
                                                    "retiré(s) du sous-processus Claude (abonnement uniquement)"))
    else:
        checks.append((OK, "Facturation à l'usage", "aucune clé API payante dans l'environnement"))
    return checks


def _jev_checks(mock: bool, jev: Optional[JevClient]) -> List[Check]:
    if mock:
        return [(WARN, "TypeSafe Jev", "SIMULATION (--mock) : validations NON fiables, aucun appel réseau")]
    try:
        client = jev or JevClient(mock_mode=False)
    except JevConfigError as e:
        return [(FAIL, "TypeSafe Jev", str(e))]
    try:
        decision = client.ping()
    except JevAuthError as e:
        return [(FAIL, "TypeSafe Jev", str(e))]
    except JevApiError as e:
        return [(WARN, "TypeSafe Jev", f"clé configurée mais service injoignable : {e}")]
    return [(OK, "TypeSafe Jev", f"clé valide, service joignable ({decision.latency_ms} ms)")]


def collect_checks(
    client: Optional[ClaudeCliClient] = None,
    allow_api_key: bool = False,
    mock: bool = False,
    jev: Optional[JevClient] = None,
) -> List[Check]:
    client = client or ClaudeCliClient(binary_path=CLAUDE_BIN_PATH, allow_api_key=allow_api_key)
    python_ok = sys.version_info >= (3, 10)
    checks: List[Check] = [(
        OK if python_ok else FAIL,
        "Python",
        f"{sys.version.split()[0]}" + ("" if python_ok else " (3.10 minimum requis)"),
    )]
    checks += _git_checks()
    checks += _claude_checks(client, allow_api_key)
    checks += _jev_checks(mock, jev)
    return checks


def run_doctor(
    client: Optional[ClaudeCliClient] = None,
    allow_api_key: bool = False,
    out: Callable[[str], None] = print,
    mock: bool = False,
    jev: Optional[JevClient] = None,
) -> int:
    """Affiche le diagnostic et retourne 0 si aucun point bloquant, 1 sinon."""
    out("\n🩺 DIAGNOSTIC WORKFLOW-CLAUDE-CODE\n" + "=" * 60)
    checks = collect_checks(client, allow_api_key, mock=mock, jev=jev)
    for level, label, detail in checks:
        out(f"{_ICONS[level]} {label:<28} {detail}")
    failures = sum(1 for level, _, _ in checks if level == FAIL)
    warnings = sum(1 for level, _, _ in checks if level == WARN)
    out("=" * 60)
    if failures:
        out(f"❌ {failures} point(s) bloquant(s), {warnings} avertissement(s).")
        return 1
    out(f"✅ Environnement utilisable ({warnings} avertissement(s)).")
    return 0

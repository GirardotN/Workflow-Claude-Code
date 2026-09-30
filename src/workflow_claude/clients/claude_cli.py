"""
Client d'exécution pour Claude Code CLI en mode headless (-p / print).
Garantit l'absence d'utilisation de crédits API payants en s'appuyant
exclusivement sur la session locale active (abonnement Claude Max 5x) :
les variables d'environnement qui activent une facturation à l'usage sont
retirées du sous-processus (voir `sanitized_env`).

Le prompt est transmis sur l'entrée standard (et non en argument) : pas de limite de taille de
ligne de commande sous Windows, et aucun contenu utilisateur n'est interprété par un shell.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ..config import (
    ALLOW_API_KEY,
    CLAUDE_BIN_PATH,
    CLAUDE_MAX_RETRIES,
    CLAUDE_TIMEOUT_DEV_SECONDS,
    CLAUDE_TIMEOUT_SECONDS,
    CLAUDE_TIMEOUT_SPEC_SECONDS,
    MOCK_SERVICES,
)
from ..roles import Role

logger = logging.getLogger("claude_cli")

# Variables qui feraient facturer l'appel à l'usage (API développeur ou cloud) au lieu de l'abonnement
BILLING_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)

# Caractères qu'un shell cmd.exe interpréterait : interdits dans les arguments si on doit passer par cmd
_CMD_UNSAFE_CHARS = set('&|<>^%"\r\n')


class ClaudeCliError(RuntimeError):
    """Exception levée en cas d'échec d'exécution du CLI Claude Code."""
    pass


class ClaudeAuthError(ClaudeCliError):
    """Session Claude Code absente ou expirée (`claude auth login`)."""
    pass


class ClaudeQuotaError(ClaudeCliError):
    """Limite d'usage de l'abonnement atteinte : inutile de réessayer tout de suite."""
    pass


class ClaudeTimeoutError(ClaudeCliError):
    """Le CLI Claude n'a pas répondu dans le délai imparti."""
    pass


@dataclass
class ClaudeResult:
    """Résultat structuré d'un appel (extrait du JSON renvoyé par `--output-format json`)."""
    text: str
    is_error: bool = False
    cost_usd: Optional[float] = None
    duration_ms: Optional[int] = None
    num_turns: Optional[int] = None
    session_id: Optional[str] = None
    api_error_status: Optional[int] = None
    terminal_reason: Optional[str] = None
    permission_denials: List[dict] = field(default_factory=list)


def detected_billing_env() -> List[str]:
    """Noms des variables d'environnement « payantes » présentes (et non vides) dans le processus."""
    return [name for name in BILLING_ENV_VARS if os.environ.get(name)]


def sanitized_env(allow_api_key: bool = False) -> Dict[str, str]:
    """
    Environnement du sous-processus Claude. Sauf `allow_api_key`, les variables de facturation
    à l'usage sont retirées : le CLI utilise alors uniquement la session de l'abonnement.
    """
    env = dict(os.environ)
    if not allow_api_key:
        for name in BILLING_ENV_VARS:
            env.pop(name, None)
    return env


def resolve_binary(binary_path: str) -> List[str]:
    """
    Retourne la commande de base pour lancer Claude. Sous Windows, le shim npm `claude.cmd` ne fait
    que lancer `node_modules/@anthropic-ai/claude-code/bin/claude.exe` : on appelle directement ce
    binaire natif, ce qui évite cmd.exe. À défaut, on garde `cmd /d /c <shim>`.
    """
    if os.name == "nt" and binary_path.lower().endswith((".cmd", ".bat")):
        shim = Path(binary_path)
        native = shim.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if native.is_file():
            return [str(native)]
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", binary_path]
    return [binary_path]


def _assert_cmd_safe(args: List[str]) -> None:
    """Refuse les arguments que cmd.exe interpréterait (le prompt, lui, passe par stdin)."""
    for arg in args:
        if any(ch in _CMD_UNSAFE_CHARS for ch in arg):
            raise ClaudeCliError(f"Argument refusé (caractère interprété par cmd.exe) : {arg[:60]!r}")


_AUTH_PATTERNS = re.compile(r"not logged in|please run /login|invalid api key|authentication|unauthorized|oauth token", re.I)
_QUOTA_PATTERNS = re.compile(r"usage limit|hit your limit|limit reached|rate limit|resets? (at|in)|credit balance|quota", re.I)
_TRANSIENT_PATTERNS = re.compile(r"overloaded|temporarily unavailable|internal server error|bad gateway|service unavailable|\b(502|503|529)\b", re.I)


class ClaudeCliClient:
    """
    Interface pour exécuter des requêtes vers Claude via le binaire local `claude`.
    """

    def __init__(
        self,
        binary_path: str = CLAUDE_BIN_PATH,
        timeout_seconds: int = CLAUDE_TIMEOUT_SECONDS,
        mock_mode: bool = MOCK_SERVICES,
        allow_api_key: bool = ALLOW_API_KEY,
        max_retries: int = CLAUDE_MAX_RETRIES,
        role_timeouts: Optional[Dict[Role, int]] = None,
    ):
        self.binary_path = binary_path
        self.timeout_seconds = timeout_seconds
        self.mock_mode = mock_mode
        self.allow_api_key = allow_api_key
        self.max_retries = max_retries
        self.role_timeouts = role_timeouts or {
            Role.SPEC: CLAUDE_TIMEOUT_SPEC_SECONDS,
            Role.DEV: CLAUDE_TIMEOUT_DEV_SECONDS,
        }
        self.last_result: Optional[ClaudeResult] = None

    def timeout_for(self, role: Optional[Role]) -> int:
        """Délai d'expiration applicable à un rôle (le délai par défaut sinon)."""
        return self.role_timeouts.get(role, self.timeout_seconds) if role else self.timeout_seconds

    def build_command(
        self,
        model: str,
        system_prompt: Optional[str] = None,
        append_system_prompt: Optional[str] = None,
        tools: Optional[str] = None,
        permission_mode: Optional[str] = None,
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
    ) -> List[str]:
        """
        Construit la ligne de commande SANS le prompt (transmis sur stdin).
        Les options à valeurs multiples utilisent la forme `--option=valeur` : le CLI les déclare
        variadiques et avalerait sinon l'argument suivant.
        """
        cmd = resolve_binary(self.binary_path) + [
            "-p",
            "--model", model,
            "--output-format", "json",
            "--no-session-persistence",
        ]
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])
        if append_system_prompt:
            cmd.extend(["--append-system-prompt", append_system_prompt])
        if tools is not None:
            cmd.append(f"--tools={tools}")  # "" = aucun outil
        if allowed_tools:
            cmd.append(f"--allowedTools={','.join(allowed_tools)}")
        if disallowed_tools:
            cmd.append(f"--disallowedTools={','.join(disallowed_tools)}")
        if permission_mode:
            cmd.extend(["--permission-mode", permission_mode])
        return cmd

    def run(
        self,
        prompt: str,
        model: str,
        system_prompt: Optional[str] = None,
        cwd: Optional[str] = None,
        tools: Optional[str] = None,
        permission_mode: Optional[str] = None,
        role: Optional[Role] = None,
        append_system_prompt: Optional[str] = None,
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
        timeout: Optional[int] = None,
    ) -> str:
        """
        Exécute une invite via le CLI Claude en mode headless (-p) et retourne le texte de la réponse.
        Lève ClaudeAuthError / ClaudeQuotaError / ClaudeTimeoutError / ClaudeCliError.
        """
        if self.mock_mode:
            return self._mock_response(prompt, model, cwd=cwd)

        cmd = self.build_command(
            model,
            system_prompt=system_prompt,
            append_system_prompt=append_system_prompt,
            tools=tools,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
            disallowed_tools=disallowed_tools,
        )
        if os.name == "nt" and cmd and cmd[0].lower().endswith("cmd.exe"):
            _assert_cmd_safe(cmd[4:])  # tout ce qui suit « cmd /d /c <shim> »

        effective_timeout = timeout or self.timeout_for(role)
        attempts = 1 + max(0, self.max_retries)
        last_error: Optional[ClaudeCliError] = None

        for attempt in range(1, attempts + 1):
            try:
                result = self._execute(cmd, prompt, cwd, effective_timeout, model)
            except ClaudeTimeoutError:
                raise
            except ClaudeCliError as e:
                if not self._is_transient(e) or attempt == attempts:
                    raise
                last_error = e
                delay = 2 * attempt
                logger.warning(f"Erreur transitoire de Claude ({e}). Nouvelle tentative {attempt}/{attempts - 1} dans {delay}s...")
                time.sleep(delay)
                continue

            self.last_result = result
            if result.permission_denials:
                denied = ", ".join(str(d.get("tool_name", "?")) for d in result.permission_denials)
                logger.warning(f"Claude s'est vu refuser l'usage d'outils par la politique de permissions : {denied}")
            return result.text

        raise last_error or ClaudeCliError("Échec inattendu de Claude CLI.")

    # ------------------------------------------------------------------
    # Exécution
    # ------------------------------------------------------------------
    def _execute(self, cmd: List[str], prompt: str, cwd: Optional[str], timeout: int, model: str) -> ClaudeResult:
        logger.debug(f"Exécution Claude CLI : model={model}, cwd={cwd}, prompt={len(prompt)} caractères (stdin)")
        start_t = time.perf_counter()
        try:
            # Octets exacts : en mode texte, Python convertirait chaque 
 du prompt en 
 sous Windows.
            raw = subprocess.run(
                cmd,
                input=prompt.encode("utf-8"),
                capture_output=True,
                shell=False,
                cwd=cwd,
                env=sanitized_env(self.allow_api_key),
                timeout=timeout,
            )
            proc = subprocess.CompletedProcess(
                raw.args,
                raw.returncode,
                stdout=raw.stdout.decode("utf-8", errors="replace"),
                stderr=raw.stderr.decode("utf-8", errors="replace"),
            )
        except FileNotFoundError:
            raise ClaudeCliError(
                f"Binaire Claude introuvable au chemin : '{self.binary_path}'. "
                "Assurez-vous que Claude Code est installé et accessible dans le PATH "
                "ou définissez la variable d'environnement CLAUDE_BIN."
            )
        except subprocess.TimeoutExpired:
            raise ClaudeTimeoutError(
                f"Timeout ({timeout}s) dépassé lors de l'exécution de Claude CLI (modèle {model}). "
                "Augmentez CLAUDE_TIMEOUT_DEV_SECONDS / CLAUDE_TIMEOUT_SPEC_SECONDS / CLAUDE_TIMEOUT_SECONDS si la tâche est longue."
            )

        logger.debug(f"Claude CLI terminé en {time.perf_counter() - start_t:.2f}s avec le code {proc.returncode}")

        data = self._parse_json(proc.stdout)
        result = self._to_result(data) if data is not None else None

        # Le CLI renvoie un JSON (is_error=true) même en cas d'échec : on le lit avant le code de sortie.
        if result is not None and result.is_error:
            raise self._classify_error(result.text, result.api_error_status)
        if proc.returncode != 0:
            message = (result.text if result else "") or proc.stderr.strip() or proc.stdout.strip()
            raise self._classify_error(message, None, returncode=proc.returncode)

        if result is None:  # sortie texte brute (ancienne version du CLI, préambule...)
            return ClaudeResult(text=proc.stdout.strip())
        return result

    @staticmethod
    def _to_result(data: dict) -> ClaudeResult:
        text = ""
        for key in ("result", "text", "content"):
            if key in data:
                text = str(data[key]).strip()
                break
        denials = data.get("permission_denials")
        status = data.get("api_error_status")
        return ClaudeResult(
            text=text,
            is_error=bool(data.get("is_error", False)),
            cost_usd=data.get("total_cost_usd"),
            duration_ms=data.get("duration_ms"),
            num_turns=data.get("num_turns"),
            session_id=data.get("session_id"),
            api_error_status=status if isinstance(status, int) else None,
            terminal_reason=data.get("terminal_reason"),
            permission_denials=denials if isinstance(denials, list) else [],
        )

    @staticmethod
    def _classify_error(message: str, status: Optional[int], returncode: Optional[int] = None) -> ClaudeCliError:
        message = (message or "").strip() or "erreur inconnue"
        if _AUTH_PATTERNS.search(message) or status in (401, 403):
            return ClaudeAuthError(
                f"Session Claude Code non authentifiée. Erreur : {message}. "
                "Exécutez `claude auth login` dans votre terminal pour activer votre session Claude Max 5x."
            )
        if _QUOTA_PATTERNS.search(message) or status == 429:
            return ClaudeQuotaError(
                f"Limite d'usage de l'abonnement Claude atteinte : {message}. "
                "Relancez après la réinitialisation indiquée, ou utilisez un modèle moins gourmand."
            )
        suffix = f" (code {returncode})" if returncode is not None else ""
        return ClaudeCliError(f"Claude CLI a échoué{suffix} : {message}")

    @staticmethod
    def _is_transient(error: ClaudeCliError) -> bool:
        if isinstance(error, (ClaudeAuthError, ClaudeQuotaError, ClaudeTimeoutError)):
            return False
        return bool(_TRANSIENT_PATTERNS.search(str(error)))

    # ------------------------------------------------------------------
    # Analyse de la sortie
    # ------------------------------------------------------------------
    @staticmethod
    def _accepts(data) -> bool:
        return isinstance(data, dict) and any(key in data for key in ("result", "text", "content"))

    def _parse_json(self, raw_stdout: str) -> Optional[dict]:
        """
        Extrait l'objet JSON de réponse de la sortie du CLI. Gère le JSON direct, une ligne JSON
        précédée de logs, et un objet noyé dans du texte (parcours lexical des accolades équilibrées).
        """
        raw_stdout = raw_stdout.strip()
        if not raw_stdout:
            return None

        # Tentative 1 : Décodage JSON direct
        try:
            data = json.loads(raw_stdout)
            if self._accepts(data):
                return data
        except json.JSONDecodeError:
            pass

        # Tentative 2 : Recherche d'un objet JSON ligne par ligne (en partant de la fin)
        for line in reversed(raw_stdout.splitlines()):
            line_s = line.strip()
            if line_s.startswith("{") and line_s.endswith("}"):
                try:
                    data = json.loads(line_s)
                    if self._accepts(data):
                        return data
                except json.JSONDecodeError:
                    continue

        # Tentative 3 : Extraction lexicale par profondeur d'accolades équilibrées
        # Évite les erreurs des regex non-gloutonnes face aux accolades imbriquées dans le code
        start_idx = raw_stdout.find("{")
        while start_idx != -1:
            depth = 0
            in_string = False
            escape = False
            for i in range(start_idx, len(raw_stdout)):
                char = raw_stdout[i]
                if escape:
                    escape = False
                    continue
                if char == "\\":
                    escape = True
                    continue
                if char == '"':
                    in_string = not in_string
                    continue
                if not in_string:
                    if char == "{":
                        depth += 1
                    elif char == "}":
                        depth -= 1
                        if depth == 0:
                            candidate = raw_stdout[start_idx: i + 1]
                            try:
                                data = json.loads(candidate)
                                if self._accepts(data):
                                    return data
                            except json.JSONDecodeError:
                                pass
                            break
            start_idx = raw_stdout.find("{", start_idx + 1)
        return None

    def _extract_result(self, raw_stdout: str) -> str:
        """Texte de la réponse : champ result/text/content du JSON, sinon la sortie brute."""
        raw_stdout = raw_stdout.strip()
        data = self._parse_json(raw_stdout)
        if data is None:
            return raw_stdout
        return self._to_result(data).text

    # ------------------------------------------------------------------
    # Diagnostic (aucun quota consommé)
    # ------------------------------------------------------------------
    def _diagnostic_run(self, extra_args: List[str], timeout: int) -> Optional[subprocess.CompletedProcess]:
        try:
            return subprocess.run(
                resolve_binary(self.binary_path) + extra_args,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                env=sanitized_env(self.allow_api_key), timeout=timeout, shell=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return None

    def version(self, timeout: int = 20) -> Optional[str]:
        """Version du CLI (`claude --version`), ou None s'il est introuvable / inutilisable."""
        proc = self._diagnostic_run(["--version"], timeout)
        if proc is None or proc.returncode != 0:
            return None
        return proc.stdout.strip() or None

    def supported_flags(self, flags: List[str], timeout: int = 30) -> Dict[str, bool]:
        """Indique, pour chaque option demandée, si elle apparaît dans `claude --help`."""
        proc = self._diagnostic_run(["--help"], timeout)
        help_text = (proc.stdout + proc.stderr) if proc else ""
        return {flag: flag in help_text for flag in flags}

    def auth_status(self, timeout: int = 30) -> Optional[dict]:
        """Résultat de `claude auth status`, ou None si indisponible."""
        proc = self._diagnostic_run(["auth", "status"], timeout)
        if proc is None:
            return None
        try:
            data = json.loads(proc.stdout)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def binary_found(binary_path: str) -> bool:
        """Vrai si le binaire est un fichier existant ou se trouve dans le PATH."""
        return Path(binary_path).is_file() or shutil.which(binary_path) is not None

    def _mock_response(self, prompt: str, model: str, cwd: Optional[str] = None) -> str:
        """Génère une réponse synthétique cohérente pour tests et simulation."""
        prompt_lower = prompt.lower()

        # Phase In-Repo : Exploration & Localisation
        if "explore le codebase" in prompt_lower:
            target_file = "src/components/TabX.tsx"
            if cwd:
                cwd_path = Path(cwd)
                # sorted() : l'ordre de rglob dépend du système de fichiers (macOS != Linux/Windows)
                all_files = sorted(
                    p for p in cwd_path.rglob("*")
                    if p.is_file() and not p.name.startswith(".") and ".git" not in p.parts
                )
                matched = None
                # 1) correspondance exacte d'abord, 2) heuristique plus large ensuite
                for f in all_files:
                    f_name = f.name.lower()
                    if "tabx" in f_name or "tab_x" in f_name:
                        matched = f
                        break
                if not matched:
                    for f in all_files:
                        if "tab" in f.name.lower() and "x" in prompt_lower:
                            matched = f
                            break
                if not matched:
                    code_files = [f for f in all_files if f.suffix in (".tsx", ".ts", ".py", ".js", ".cs")]
                    if code_files:
                        matched = code_files[0]
                if not matched and all_files:
                    matched = all_files[0]

                if matched:
                    target_file = matched.relative_to(cwd_path).as_posix()

            return (
                f"# Spécification & Localisation In-Situ\n\n"
                f"## Fichiers Cibles Identifiés\n- `{target_file}`\n\n"
                f"## Analyse du Code Existant\nComposant localisé avec fonction de traitement/tri ciblée.\n\n"
                f"## Plan de Modification\n1. Modifier la fonction ciblée dans `{target_file}`.\n"
                f"2. Assurer la conformité du contrat d'interface et l'absence de régression."
            )

        # Phase In-Repo : Développement In-Situ
        if "applique directement les modifications" in prompt_lower or "in-situ" in prompt_lower:
            if cwd:
                cwd_path = Path(cwd)
                # sorted() : l'ordre de rglob dépend du système de fichiers (macOS != Linux/Windows)
                all_files = sorted(
                    p for p in cwd_path.rglob("*")
                    if p.is_file() and not p.name.startswith(".") and ".git" not in p.parts
                )
                target = None
                for f in all_files:
                    rel_name = f.relative_to(cwd_path).as_posix()
                    if rel_name in prompt or f.name in prompt:
                        target = f
                        break
                    if "tabx" in f.name.lower() or "tab_x" in f.name.lower():
                        target = f
                        break

                if not target:
                    code_files = [f for f in all_files if f.suffix in (".tsx", ".ts", ".py", ".js", ".cs")]
                    if code_files:
                        target = code_files[0]
                if not target and all_files:
                    target = all_files[0]

                if target:
                    content = target.read_text(encoding="utf-8", errors="replace")
                    if "sort" in content:
                        new_content = content.replace(".sort(", ".sort((a, b) => b.date - a.date) /* in-situ edit */\n// ")
                        if new_content == content:
                            new_content = content.replace("sort(", "sort((a, b) => b.date - a.date) /* in-situ edit */\n// ")
                    else:
                        new_content = content + "\n\n// Modification in-situ appliquée avec succès (date décroissante)\nexport const sortItems = (items) => [...items].sort((a, b) => b.date - a.date);\n"
                    target.write_text(new_content, encoding="utf-8")
                    return f"Modifications in-situ appliquées avec succès dans : {target.relative_to(cwd_path)}"

            return "Modifications in-situ appliquées avec succès dans le projet."

        if "lead software architect" in prompt_lower or "spécification technique d'implémentation" in prompt_lower:
            return (
                "# Spécification Technique Détaillée\n\n"
                "## Objectifs\nImplémenter le service demandé avec une architecture modulaire et typée.\n\n"
                "## Composants Requis\n- Contrôleur principal\n- Validateur de schéma\n- Gestionnaire d'erreurs\n\n"
                "## Contraintes\n- Respect des standards de sécurité\n- Couverture de tests unitaires"
            )

        if "tu es un expert" in prompt_lower or "implémente la solution" in prompt_lower or "corrige et améliore" in prompt_lower:
            # Phase Dev
            return (
                "```python\n"
                "# Module généré automatiquement par l'agent de développement\n"
                "from typing import Any, Dict\n\n"
                "def process_task(data: Dict[str, Any]) -> Dict[str, Any]:\n"
                "    \"\"\"Traite une tâche avec validation de contrat.\"\"\"\n"
                "    if not isinstance(data, dict):\n"
                "        raise ValueError('Données invalides : dictionnaire attendu')\n"
                "    return {'status': 'success', 'processed': True, 'payload': data}\n"
                "```"
            )

        if "senior code reviewer" in prompt_lower or "qualité, robustesse" in prompt_lower:
            # Phase Check Qualité
            return (
                "ANALYSE QUALITÉ :\n"
                "- Structure du code claire et typée.\n"
                "- Validation des arguments présente.\n"
                "- Aucun bug critique détecté.\n"
                "- Conforme aux exigences."
            )

        if "cyber-sécurité" in prompt_lower or "audit de sécurité" in prompt_lower:
            # Phase Check Sécurité
            return (
                "AUDIT SÉCURITÉ :\n"
                "- Aucune injection détectée.\n"
                "- Validation stricte des entrées.\n"
                "- Pas de fuite de mémoire ou de ressources.\n"
                "- Niveau de sécurité : Conforme."
            )

        if "feedback correctif" in prompt_lower:
            # Phase Feedback
            return (
                "- Corriger la validation des entrées pour refuser les valeurs None.\n"
                "- Ajouter une gestion des exceptions pour les timeouts.\n"
                "- Respecter les types stricts."
            )

        if "technical writer & git master" in prompt_lower or "documentation technique" in prompt_lower:
            # Phase Doc & Commit
            return (
                "## DOCUMENTATION TECHNIQUE\n"
                "Module de traitement de données robuste avec typage statique et gestion d'erreurs intégrée.\n\n"
                "## PROPOSITION DE COMMIT GIT\n"
                "```git\n"
                "feat(core): implement robust data processing pipeline with validated contracts\n\n"
                "- Add schema validation and type hints\n"
                "- Handle invalid data exceptions gracefully\n"
                "```"
            )

        return f"[Simulation {model}] Réponse synthétique pour l'instruction."

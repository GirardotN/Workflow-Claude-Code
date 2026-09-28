"""
Client d'exécution pour Claude Code CLI en mode headless (-p / print).
Garantit l'absence d'utilisation de crédits API payants en s'appuyant
exclusivement sur la session locale active (abonnement Claude Max 5x).
"""

import json
import logging
import os
import re
import subprocess
import time
from typing import Optional

from config import CLAUDE_BIN_PATH, CLAUDE_TIMEOUT_SECONDS, MOCK_SERVICES

logger = logging.getLogger("claude_cli")


class ClaudeCliError(RuntimeError):
    """Exception levée en cas d'échec d'exécution du CLI Claude Code."""
    pass


class ClaudeCliClient:
    """
    Interface pour exécuter des requêtes vers Claude via le binaire local `claude`.
    """

    def __init__(
        self,
        binary_path: str = CLAUDE_BIN_PATH,
        timeout_seconds: int = CLAUDE_TIMEOUT_SECONDS,
        mock_mode: bool = MOCK_SERVICES,
    ):
        self.binary_path = binary_path
        self.timeout_seconds = timeout_seconds
        self.mock_mode = mock_mode

    def run(
        self,
        prompt: str,
        model: str,
        system_prompt: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> str:
        """
        Exécute une invite via le CLI Claude en mode headless (-p).
        """
        if self.mock_mode:
            return self._mock_response(prompt, model)

        cmd = [
            self.binary_path,
            "-p",
            prompt,
            "--model",
            model,
            "--output-format",
            "json",
            "--no-session-persistence",
        ]

        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])

        logger.debug(f"Exécution Claude CLI : model={model}, cmd={' '.join(cmd[:4])}...")
        start_t = time.perf_counter()

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError:
            raise ClaudeCliError(
                f"Binaire Claude introuvable au chemin : '{self.binary_path}'. "
                "Assurez-vous que Claude Code est installé et accessible dans le PATH "
                "ou définissez la variable d'environnement CLAUDE_BIN."
            )
        except subprocess.TimeoutExpired:
            raise ClaudeCliError(
                f"Timeout ({self.timeout_seconds}s) dépassé lors de l'exécution de Claude CLI (modèle {model})."
            )

        duration = time.perf_counter() - start_t
        logger.debug(f"Claude CLI terminé en {duration:.2f}s avec le code {proc.returncode}")

        if proc.returncode != 0:
            stderr_msg = proc.stderr.strip() or proc.stdout.strip()
            # Cas fréquent : session non authentifiée
            if "Not logged in" in stderr_msg or "Please run /login" in stderr_msg:
                raise ClaudeCliError(
                    f"Session Claude Code non authentifiée. Erreur : {stderr_msg}. "
                    "Exécutez `claude auth login` dans votre terminal pour activer votre session Claude Max 5x."
                )
            raise ClaudeCliError(f"Claude CLI a échoué (code {proc.returncode}) : {stderr_msg}")

        # Extraction de la réponse depuis la sortie JSON ou texte
        return self._extract_result(proc.stdout)

    def _extract_result(self, raw_stdout: str) -> str:
        """
        Extrait le contenu utile de la sortie du CLI.
        Gère le format JSON structuré ainsi que les éventuelles lignes de préambule.
        """
        raw_stdout = raw_stdout.strip()
        if not raw_stdout:
            return ""

        # Tentative 1 : Décodage JSON direct
        try:
            data = json.loads(raw_stdout)
            if isinstance(data, dict):
                # Claude Code JSON format standard : champ "result"
                if "result" in data:
                    return str(data["result"]).strip()
                if "text" in data:
                    return str(data["text"]).strip()
                if "content" in data:
                    return str(data["content"]).strip()
        except json.JSONDecodeError:
            pass

        # Tentative 2 : Recherche d'un bloc JSON encadré dans la sortie
        json_match = re.search(r"\{.*\}", raw_stdout, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(0))
                if isinstance(data, dict) and "result" in data:
                    return str(data["result"]).strip()
            except json.JSONDecodeError:
                pass

        # Fallback : Sortie texte directe brute
        return raw_stdout

    def _mock_response(self, prompt: str, model: str) -> str:
        """Génère une réponse synthétique cohérente pour tests et simulation."""
        prompt_lower = prompt.lower()

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

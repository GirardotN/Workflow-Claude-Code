"""
Client d'exécution pour Claude Code CLI en mode headless (-p / print).
Garantit l'absence d'utilisation de crédits API payants en s'appuyant
exclusivement sur la session locale active (abonnement Claude Max 5x).
"""

import json
import logging
import os
import subprocess
import time
from pathlib import Path
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
        tools: Optional[str] = None,
        permission_mode: Optional[str] = None,
    ) -> str:
        """
        Exécute une invite via le CLI Claude en mode headless (-p).
        """
        if self.mock_mode:
            return self._mock_response(prompt, model, cwd=cwd)

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
        if tools:
            cmd.extend(["--tools", tools])
        if permission_mode:
            cmd.extend(["--permission-mode", permission_mode])

        logger.debug(f"Exécution Claude CLI : model={model}, cmd={' '.join(cmd[:4])}...")
        start_t = time.perf_counter()

        try:
            # Sur Windows, si le binaire est un script .cmd/.bat, invocation sécurisée sans shell=True
            exec_cmd = cmd
            if os.name == "nt" and self.binary_path.lower().endswith((".cmd", ".bat")):
                comspec = os.environ.get("COMSPEC", "cmd.exe")
                exec_cmd = [comspec, "/d", "/c", self.binary_path] + cmd[1:]

            proc = subprocess.run(
                exec_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
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
                for key in ("result", "text", "content"):
                    if key in data:
                        return str(data[key]).strip()
        except json.JSONDecodeError:
            pass

        # Tentative 2 : Recherche d'un objet JSON ligne par ligne (en partant de la fin)
        for line in reversed(raw_stdout.splitlines()):
            line_s = line.strip()
            if line_s.startswith("{") and line_s.endswith("}"):
                try:
                    data = json.loads(line_s)
                    if isinstance(data, dict):
                        for key in ("result", "text", "content"):
                            if key in data:
                                return str(data[key]).strip()
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
                            candidate = raw_stdout[start_idx : i + 1]
                            try:
                                data = json.loads(candidate)
                                if isinstance(data, dict):
                                    for key in ("result", "text", "content"):
                                        if key in data:
                                            return str(data[key]).strip()
                            except json.JSONDecodeError:
                                pass
                            break
            start_idx = raw_stdout.find("{", start_idx + 1)

        # Fallback : Sortie texte directe brute
        return raw_stdout

    def _mock_response(self, prompt: str, model: str, cwd: Optional[str] = None) -> str:
        """Génère une réponse synthétique cohérente pour tests et simulation."""
        prompt_lower = prompt.lower()

        # Phase In-Repo : Exploration & Localisation
        if "explore le codebase" in prompt_lower:
            target_file = "src/components/TabX.tsx"
            if cwd:
                cwd_path = Path(cwd)
                all_files = [
                    p for p in cwd_path.rglob("*")
                    if p.is_file() and not p.name.startswith(".") and ".git" not in p.parts
                ]
                matched = None
                for f in all_files:
                    f_name = f.name.lower()
                    if "tabx" in f_name or "tab_x" in f_name or ("tab" in f_name and "x" in prompt_lower):
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
                all_files = [
                    p for p in cwd_path.rglob("*")
                    if p.is_file() and not p.name.startswith(".") and ".git" not in p.parts
                ]
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

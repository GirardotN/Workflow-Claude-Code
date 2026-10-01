"""
Simulation de Claude (mode `--mock` et tests) : réponses synthétiques cohérentes, choisies selon le
RÔLE de l'appel (et non plus selon des sous-chaînes du prompt, fragile : la revue qualité d'un diff
contenant « in-situ » déclenchait la branche du développeur).

Deux comportements pour l'agent de développement en mode In-Repo :
- `edit_files=True` (défaut, tests) : édite réellement le fichier cible identifié par la spécification ;
- `edit_files=False` (CLI `--mock`) : n'écrit qu'un fichier marqueur `WORKFLOW_MOCK.md`, pour ne jamais
  modifier le code réel d'un projet pendant une simulation.
"""

from pathlib import Path
from typing import Optional

from ..roles import Role

MOCK_MARKER_FILE = "WORKFLOW_MOCK.md"
_CODE_SUFFIXES = (".tsx", ".ts", ".py", ".js", ".cs")


def infer_role(prompt: str) -> Optional[Role]:
    """Repli pour les appels sans rôle explicite (usage direct du client) : déduit le rôle du prompt."""
    p = prompt.lower()
    if "explore le codebase" in p or "lead software architect" in p:
        return Role.SPEC
    if "technical writer & git master" in p or "documentation technique" in p:
        return Role.DOC
    if "feedback correctif" in p:
        return Role.FEEDBACK
    if "cyber-sécurité" in p or "audit de sécurité" in p:
        return Role.SECURITY
    if "senior code reviewer" in p or "qualité, robustesse" in p:
        return Role.QUALITY
    if "tu es un expert" in p or "implémente la solution" in p or "corrige et améliore" in p or "applique directement" in p:
        return Role.DEV
    return None


def _project_files(cwd: Path):
    # sorted() : l'ordre de rglob dépend du système de fichiers (macOS != Linux/Windows)
    return sorted(
        p for p in cwd.rglob("*")
        if p.is_file() and not p.name.startswith(".") and ".git" not in p.parts and p.name != MOCK_MARKER_FILE
    )


class MockClaude:
    def __init__(self, edit_files: bool = True):
        self.edit_files = edit_files

    def respond(
        self,
        prompt: str,
        model: str,
        role: Optional[Role] = None,
        cwd: Optional[str] = None,
        tools: Optional[str] = None,
        permission_mode: Optional[str] = None,
    ) -> str:
        role = role or infer_role(prompt)
        p = prompt.lower()

        if role is Role.SPEC:
            in_repo = bool(tools) or "explore le codebase" in p
            return self._spec_in_repo(prompt, cwd) if in_repo else self._spec_standalone()
        if role is Role.DEV:
            in_place = permission_mode == "acceptEdits" or "applique directement les modifications" in p
            return self._dev_in_place(prompt, cwd) if in_place else self._dev_standalone()
        if role is Role.QUALITY:
            return (
                "ANALYSE QUALITÉ :\n"
                "- Structure du code claire et typée.\n"
                "- Validation des arguments présente.\n"
                "- Aucun bug critique détecté.\n"
                "- Conforme aux exigences."
            )
        if role is Role.SECURITY:
            return (
                "AUDIT SÉCURITÉ :\n"
                "- Aucune injection détectée.\n"
                "- Validation stricte des entrées.\n"
                "- Pas de fuite de mémoire ou de ressources.\n"
                "- Niveau de sécurité : Conforme."
            )
        if role is Role.FEEDBACK:
            return (
                "- Corriger la validation des entrées pour refuser les valeurs None.\n"
                "- Ajouter une gestion des exceptions pour les timeouts.\n"
                "- Respecter les types stricts."
            )
        if role is Role.DOC:
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
        if role is Role.DOC_EDIT:
            return self._doc_edit(cwd)
        return f"[Simulation {model}] Réponse synthétique pour l'instruction."

    # ------------------------------------------------------------------
    def _spec_in_repo(self, prompt: str, cwd: Optional[str]) -> str:
        target_file = "src/components/TabX.tsx"
        if cwd:
            cwd_path = Path(cwd)
            all_files = _project_files(cwd_path)
            prompt_lower = prompt.lower()
            matched = None
            # 1) correspondance exacte d'abord, 2) heuristique plus large ensuite
            for f in all_files:
                name = f.name.lower()
                if "tabx" in name or "tab_x" in name:
                    matched = f
                    break
            if not matched:
                for f in all_files:
                    if "tab" in f.name.lower() and "x" in prompt_lower:
                        matched = f
                        break
            if not matched:
                code_files = [f for f in all_files if f.suffix in _CODE_SUFFIXES]
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

    @staticmethod
    def _spec_standalone() -> str:
        return (
            "# Spécification Technique Détaillée\n\n"
            "## Objectifs\nImplémenter le service demandé avec une architecture modulaire et typée.\n\n"
            "## Composants Requis\n- Contrôleur principal\n- Validateur de schéma\n- Gestionnaire d'erreurs\n\n"
            "## Contraintes\n- Respect des standards de sécurité\n- Couverture de tests unitaires"
        )

    @staticmethod
    def _dev_standalone() -> str:
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

    def _dev_in_place(self, prompt: str, cwd: Optional[str]) -> str:
        if not cwd:
            return "Modifications in-situ appliquées avec succès dans le projet."
        cwd_path = Path(cwd)

        if not self.edit_files:
            marker = cwd_path / MOCK_MARKER_FILE
            previous = marker.read_text(encoding="utf-8") if marker.is_file() else "# Simulation Workflow-Claude-Code\n\n"
            marker.write_text(previous + "- modification simulée (--mock) : aucun fichier du projet n'a été touché\n", encoding="utf-8")
            return f"Modification simulée : seul le fichier marqueur {MOCK_MARKER_FILE} a été écrit."

        all_files = _project_files(cwd_path)
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
            code_files = [f for f in all_files if f.suffix in _CODE_SUFFIXES]
            if code_files:
                target = code_files[0]
        if not target and all_files:
            target = all_files[0]
        if not target:
            return "Modifications in-situ appliquées avec succès dans le projet."

        content = target.read_text(encoding="utf-8", errors="replace")
        if "sort" in content:
            new_content = content.replace(".sort(", ".sort((a, b) => b.date - a.date) /* in-situ edit */\n// ")
            if new_content == content:
                new_content = content.replace("sort(", "sort((a, b) => b.date - a.date) /* in-situ edit */\n// ")
        else:
            new_content = content + "\n\n// Modification in-situ appliquée avec succès (date décroissante)\nexport const sortItems = (items) => [...items].sort((a, b) => b.date - a.date);\n"
        target.write_text(new_content, encoding="utf-8")
        return f"Modifications in-situ appliquées avec succès dans : {target.relative_to(cwd_path)}"

    def _doc_edit(self, cwd: Optional[str]) -> str:
        if not (self.edit_files and cwd):
            return "Aucune modification de documentation nécessaire."
        changelog = Path(cwd) / "CHANGELOG.md"
        header = "# Changelog\n\n## [Non publié]\n\n"
        previous = changelog.read_text(encoding="utf-8") if changelog.is_file() else header
        changelog.write_text(previous.rstrip("\n") + "\n- Documentation mise à jour (simulation).\n", encoding="utf-8")
        return "CHANGELOG.md mis à jour."

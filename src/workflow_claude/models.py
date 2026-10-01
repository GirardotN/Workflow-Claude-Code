"""
Modèles de données, structures typées et énumérations pour l'orchestrateur multi-agents.
"""

import logging
import re
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("models")


class WorkflowType(str, Enum):
    SIMPLE = "Tâche Simple"
    MOYENNE = "Tâche Moyenne"
    COMPLEXE = "Tâche Complexe"

    @classmethod
    def from_str(cls, value: str) -> "WorkflowType":
        val_clean = value.strip().lower()
        if "simple" in val_clean:
            return cls.SIMPLE
        if "moyen" in val_clean:
            return cls.MOYENNE
        if "complex" in val_clean:
            return cls.COMPLEXE
        # Fallback par défaut sur MOYENNE si ambigu (signalé : le routage n'a pas été compris)
        logger.warning(f"Niveau de complexité non reconnu ({value!r}) : repli sur « {cls.MOYENNE.value} ».")
        return cls.MOYENNE


class DevSpecialty(str, Enum):
    CSHARP = "Dev C#"
    NODEJS = "Dev Node.js"
    UI = "Dev UI"
    PYTHON = "Dev Python"

    @classmethod
    def from_str(cls, value: str) -> "DevSpecialty":
        val_clean = value.strip().lower()
        if "c#" in val_clean or "csharp" in val_clean:
            return cls.CSHARP
        if re.search(r"\b(node|nodejs|js|javascript|typescript|ts)\b", val_clean):
            return cls.NODEJS
        if re.search(r"\b(ui|frontend|front-end|react|vue|html|css)\b", val_clean):
            return cls.UI
        if re.search(r"\b(python|py|fastapi|django|flask)\b", val_clean):
            return cls.PYTHON
        logger.warning(f"Spécialité non reconnue ({value!r}) : repli sur « {cls.PYTHON.value} ».")
        return cls.PYTHON  # Par défaut


@dataclass
class StepRecord:
    """Enregistrement d'une étape individuelle dans le workflow pour traçabilité."""
    step_name: str
    model: str
    prompt_sent: str
    output_received: str
    duration_seconds: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowExecutionReport:
    """Rapport d'exécution complet du workflow multi-agents."""
    prompt_simple: str
    spec_complexe: str = ""
    workflow_type: Optional[WorkflowType] = None
    dev_specialty: Optional[DevSpecialty] = None
    code_produit: str = ""
    review_qualite: str = ""
    review_securite: str = ""
    doc_et_commit: str = ""
    iterations_count: int = 0
    is_success: bool = False
    error_message: Optional[str] = None
    history: List[StepRecord] = field(default_factory=list)

    # Champs spécifiques au mode In-Repo (modifications directes dans un projet)
    is_in_repo: bool = False
    project_dir: Optional[str] = None
    git_diff: str = ""
    modified_files: List[str] = field(default_factory=list)
    commit_hash: Optional[str] = None
    branch_name: Optional[str] = None
    original_branch: Optional[str] = None
    tests_passed: Optional[bool] = None
    tests_output: str = ""
    baseline_tests_passed: Optional[bool] = None
    baseline_tests_failed: List[str] = field(default_factory=list)  # tests déjà en échec avant toute modification
    tests_failed: List[str] = field(default_factory=list)           # tests en échec au dernier cycle
    merged: bool = False                          # branche d'isolation fusionnée dans la branche d'origine
    cost_usd: float = 0.0                         # coût cumulé rapporté par le CLI Claude (équivalent API, informatif)
    doc_files_kept: List[str] = field(default_factory=list)      # fichiers de documentation mis à jour par l'agent doc
    doc_files_reverted: List[str] = field(default_factory=list)  # modifications hors liste blanche annulées
    doc_diff: str = ""                             # diff de la seule documentation
    jev_mode: str = ""                            # "live" (TypeSafe) ou "mock" (simulation, validations NON fiables)
    decisions: List[dict] = field(default_factory=list)  # décisions Jev : étape, résultat, probabilité, latence, tokens
    stash_restored: Optional[bool] = None         # None = aucun stash créé ; False = conflit (stash conservé)

    # Champs textuels volumineux (spécification, code, revues, diff, sorties) : exclus du résumé par défaut
    _TEXT_FIELDS = (
        "spec_complexe", "code_produit", "review_qualite", "review_securite", "doc_et_commit",
        "git_diff", "tests_output", "doc_diff",
    )

    def to_dict(self, include_texts: bool = False) -> dict:
        """
        Représentation sérialisable en JSON du rapport. Par défaut sans les textes volumineux ni le contenu des
        prompts/sorties des étapes (qui peuvent contenir du code ou des secrets) : durées, modèles, coûts et
        décisions suffisent à l'audit.
        """
        data = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name in self._TEXT_FIELDS and not include_texts:
                continue
            if f.name == "history":
                value = [
                    {
                        "step_name": s.step_name,
                        "model": s.model,
                        "duration_seconds": round(s.duration_seconds, 3),
                        "metadata": s.metadata,
                        **({"prompt_sent": s.prompt_sent, "output_received": s.output_received} if include_texts else {}),
                    }
                    for s in value
                ]
            elif isinstance(value, Enum):
                value = value.value
            data[f.name] = value
        return data


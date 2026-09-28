"""
Modèles de données, structures typées et énumérations pour l'orchestrateur multi-agents.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


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
        # Fallback par défaut sur MOYENNE si ambigu
        return cls.MOYENNE


class DevSpecialty(str, Enum):
    CSHARP = "Dev C#"
    NODEJS = "Dev Node.js"
    UI = "Dev UI"
    PYTHON = "Dev Python"

    @classmethod
    def from_str(cls, value: str) -> "DevSpecialty":
        import re
        val_clean = value.strip().lower()
        if "c#" in val_clean or "csharp" in val_clean:
            return cls.CSHARP
        if re.search(r"\b(node|nodejs|js|javascript|typescript|ts)\b", val_clean):
            return cls.NODEJS
        if re.search(r"\b(ui|frontend|front-end|react|vue|html|css)\b", val_clean):
            return cls.UI
        if re.search(r"\b(python|py|fastapi|django|flask)\b", val_clean):
            return cls.PYTHON
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
    target_files: List[str] = field(default_factory=list)
    git_diff: str = ""
    modified_files: List[str] = field(default_factory=list)
    commit_hash: Optional[str] = None

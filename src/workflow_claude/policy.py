"""
Matrice d'allocation des modèles : source de vérité unique (voir docs/architecture.md, « Matrice
d'Allocation des Modèles »). Un test compare cette politique au tableau de la documentation.

| Étape / Rôle               | Simple | Moyenne | Complexe |
| 1. Spécification            | Sonnet | Sonnet  | Sonnet   |
| 3. Agent de développement   | Sonnet | Sonnet  | Opus     |
| 5. Audit bug & qualité      | Sonnet | Sonnet  | Opus     |
| 7. Synthèse feedback qualité| Haiku  | Haiku   | Sonnet   |
| 8. Audit cyber-sécurité     | (non)  | Sonnet  | Sonnet   |
| 10. Synthèse feedback sécu  | (non)  | Haiku   | Sonnet   |
| 11. Documentation & commit  | Sonnet | Haiku   | Haiku    |
"""

from dataclasses import dataclass

from .config import MODEL_HAIKU, MODEL_OPUS, MODEL_SONNET
from .models import WorkflowType


@dataclass(frozen=True)
class ModelPolicy:
    """Modèle Claude à utiliser pour chaque rôle, selon la complexité décidée par Jev."""

    spec: str
    dev: str
    quality: str
    feedback_quality: str
    security: str
    feedback_security: str
    doc: str
    run_security: bool

    @classmethod
    def for_type(cls, workflow_type: WorkflowType) -> "ModelPolicy":
        is_complexe = workflow_type == WorkflowType.COMPLEXE
        is_simple = workflow_type == WorkflowType.SIMPLE
        return cls(
            spec=MODEL_SONNET,
            dev=MODEL_OPUS if is_complexe else MODEL_SONNET,
            quality=MODEL_OPUS if is_complexe else MODEL_SONNET,
            feedback_quality=MODEL_SONNET if is_complexe else MODEL_HAIKU,
            security=MODEL_SONNET,
            feedback_security=MODEL_SONNET if is_complexe else MODEL_HAIKU,
            doc=MODEL_SONNET if is_simple else MODEL_HAIKU,
            run_security=not is_simple,
        )

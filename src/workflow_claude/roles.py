"""
Rôles des appels à Claude dans la machine à états.

Le rôle détermine le délai d'expiration, les outils autorisés et l'environnement d'exécution
(voir `tool_policy.py`). Il servira aussi de clé aux simulations (mocks) en remplacement du
filtrage fragile sur le texte des prompts.
"""

from enum import Enum


class Role(str, Enum):
    SPEC = "spec"          # exploration du dépôt + spécification (outils en lecture seule)
    DEV = "dev"            # modification des fichiers du projet
    QUALITY = "quality"    # revue qualité d'un diff (aucun outil, aucun accès aux fichiers)
    SECURITY = "security"  # revue sécurité d'un diff (aucun outil, aucun accès aux fichiers)
    FEEDBACK = "feedback"  # synthèse d'un feedback correctif (aucun outil)
    DOC = "doc"            # documentation et message de commit (aucun outil)

    @property
    def is_isolated_reviewer(self) -> bool:
        """Rôles qui ne doivent voir QUE le texte qu'on leur envoie (isolation cognitive)."""
        return self in (Role.QUALITY, Role.SECURITY, Role.FEEDBACK, Role.DOC)

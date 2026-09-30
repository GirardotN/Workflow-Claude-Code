"""
Gabarits de prompts des agents, regroupés et versionnés au même endroit.

Contrats implicites (vérifiés par des tests) :
- le prompt de qualité ne contient JAMAIS le cahier des charges ni le rôle du développeur
  (isolation cognitive) ; il se termine par la consigne VERDICT ;
- le prompt de sécurité reçoit le code/diff ET la revue qualité préalable ;
- les retours correctifs sont des listes à puces concises, sans historique.
"""

from typing import Optional

from .jev_context import truncate_middle
from .models import DevSpecialty

PROMPTS_VERSION = "2"

# Les relecteurs terminent par un verdict lisible par machine ; il sert de contrôle de cohérence avec Jev.
VERDICT_INSTRUCTION = (
    "\n\nTermine OBLIGATOIREMENT ta réponse par une dernière ligne exactement `VERDICT: PASS` "
    "(aucun défaut bloquant) ou `VERDICT: FAIL` (au moins un défaut bloquant)."
)

# Consignes de spécialité ajoutées au prompt système de l'agent de développement (--append-system-prompt)
SPECIALTY_SYSTEM_PROMPTS = {
    DevSpecialty.PYTHON.value: (
        "Tu écris du Python idiomatique (PEP 8, annotations de types, docstrings concises). "
        "Respecte le style, les dépendances et la structure de tests déjà présents dans le projet."
    ),
    DevSpecialty.NODEJS.value: (
        "Tu écris du JavaScript/TypeScript pour Node.js. Respecte le gestionnaire de paquets, la configuration "
        "TypeScript/ESLint et les conventions du projet ; ne modifie pas les lockfiles à la main."
    ),
    DevSpecialty.UI.value: (
        "Tu travailles sur une interface web (React, Vue, HTML, CSS). Respecte le système de composants, le style "
        "et les conventions d'accessibilité du projet ; ne change pas l'API publique des composants sans nécessité."
    ),
    DevSpecialty.CSHARP.value: (
        "Tu écris du C# / .NET moderne. Respecte les conventions de nommage, l'injection de dépendances et les "
        "projets de tests déjà présents ; ne modifie pas les fichiers générés."
    ),
}


def specialty_system_prompt(specialty: DevSpecialty) -> Optional[str]:
    return SPECIALTY_SYSTEM_PROMPTS.get(specialty.value)


# ---------------------------------------------------------------------------
# Spécification
# ---------------------------------------------------------------------------
def spec_in_repo(request: str) -> str:
    return (
        "Tu es un Lead Software Architect. Le projet cible se trouve dans le répertoire courant.\n"
        f"Demande utilisateur : {request}\n\n"
        "Explore le codebase à l'aide de tes outils pour localiser précisément les fichiers, "
        "fonctions ou composants concernés par cette demande.\n"
        "IMPORTANT : Ignore impérativement les répertoires et artefacts volumineux ou générés : "
        "node_modules, .git, dist, build, .next, .venv, venv, __pycache__, .pytest_cache, coverage, bin, obj.\n\n"
        "Rédige une spécification technique d'implémentation in-situ complète :\n"
        "1. Fichiers et fonctions cibles identifiés (chemins relatifs précis)\n"
        "2. Analyse de l'implémentation actuelle\n"
        "3. Plan d'édition chirurgicale requis\n"
        "4. Contrats d'interfaces et critères de non-régression."
    )


def spec_standalone(request: str) -> str:
    return (
        "Tu es un Lead Software Architect. À partir de la demande succincte suivante, "
        "génère une spécification technique d'implémentation complète, claire et structurée "
        "(objectifs, architecture, contrats d'interface, cas d'erreurs, critères d'acceptation) :\n\n"
        f"Demande : {request}"
    )


# ---------------------------------------------------------------------------
# Développement
# ---------------------------------------------------------------------------
def dev_in_repo(specialty: DevSpecialty, spec: str, feedback: str = "") -> str:
    if not feedback:
        return (
            f"Tu es un expert {specialty.value}.\n"
            "Applique directement les modifications demandées dans les fichiers du projet "
            "conformément au cahier des charges ci-dessous :\n\n"
            f"CAHIER DES CHARGES :\n{spec}\n\n"
            "Utilise tes outils pour modifier chirurgicalement les fichiers en place. "
            "Ne modifie que ce qui est strictement nécessaire et respecte l'architecture existante."
        )
    return (
        f"Tu es un expert {specialty.value}.\n"
        "La tentative précédente a rencontré un problème ou a été rejetée. "
        "Corrige le tir directement dans les fichiers du projet :\n\n"
        f"CAHIER DES CHARGES :\n{spec}\n\n"
        f"RETOURS OBLIGATOIRES À CORRIGER :\n{feedback}\n\n"
        "Applique les corrections nécessaires dans les fichiers du projet."
    )


def dev_standalone(specialty: DevSpecialty, spec: str, previous_code: str = "", feedback: str = "") -> str:
    if not feedback:
        return (
            f"Tu es un expert {specialty.value}.\n"
            "Implémente la solution complète et rigoureuse répondant au cahier des charges ci-dessous.\n\n"
            f"CAHIER DES CHARGES :\n{spec}\n\n"
            "Fournis le code complet prêt pour la production."
        )
    return (
        f"Tu es un expert {specialty.value}.\n"
        "Corrige et améliore le code existant pour résoudre rigoureusement les retours formulés ci-dessous.\n\n"
        f"CAHIER DES CHARGES :\n{spec}\n\n"
        f"CODE ACTUEL :\n{previous_code}\n\n"
        f"RETOURS OBLIGATOIRES À CORRIGER :\n{feedback}\n\n"
        "Fournis la nouvelle version complète et corrigée du code."
    )


def tests_failed_feedback(command: str, output: str) -> str:
    return (
        "La modification apportée a provoqué des régressions ou des échecs dans les tests du projet.\n"
        f"COMMANDE DE TEST : {command}\n\n"
        f"TRACE D'ERREUR DES TESTS :\n{output}\n\n"
        "Corrige immédiatement le code dans les fichiers du projet pour que les tests réussissent."
    )


# ---------------------------------------------------------------------------
# Revues (isolées : ne reçoivent que le diff / le code)
# ---------------------------------------------------------------------------
def quality_in_repo(diff: str, max_chars: int) -> str:
    return (
        "Tu es un Senior Code Reviewer. Analyse uniquement le git diff ci-dessous représentant "
        "les modifications apportées au projet pour évaluer la qualité, la robustesse, "
        "l'absence de régression et le respect des conventions existantes. Sois intraitable sur les bugs :\n\n"
        f"GIT DIFF :\n{truncate_middle(diff, max_chars)}"
        f"{VERDICT_INSTRUCTION}"
    )


def quality_standalone(code: str, max_chars: int) -> str:
    return (
        "Tu es un Senior Code Reviewer. Analyse uniquement le code fourni ci-dessous "
        "pour évaluer la qualité, la robustesse, la conformité aux bonnes pratiques "
        "et détecter tout bug ou régression potentielle. Sois intraitable sur les bugs :\n\n"
        f"CODE À ANALYSER :\n{truncate_middle(code, max_chars)}"
        f"{VERDICT_INSTRUCTION}"
    )


def security_in_repo(diff: str, quality_review: str, max_chars: int) -> str:
    return (
        "Tu es un Expert en Cyber-Sécurité logicielle (AppSec). Analyse en profondeur "
        "ce git diff ainsi que sa review qualité préalable. Détecte toute vulnérabilité potentielle "
        "(injections, failles logiques, fuite de données, gestion non sécurisée des secrets, régressions) :\n\n"
        f"GIT DIFF :\n{truncate_middle(diff, max_chars)}\n\n"
        f"REVIEW QUALITÉ PRÉALABLE :\n{quality_review}"
        f"{VERDICT_INSTRUCTION}"
    )


def security_standalone(code: str, quality_review: str, max_chars: int) -> str:
    return (
        "Tu es un Expert en Cyber-Sécurité logicielle (AppSec). Analyse en profondeur "
        "ce code ainsi que sa review qualité préalable. Détecte les vulnérabilités potentielles "
        "(injections, failles logiques, fuites de données, gestion non sécurisée des secrets, déni de service) :\n\n"
        f"CODE PRODUIT :\n{truncate_middle(code, max_chars)}\n\n"
        f"REVIEW QUALITÉ PRÉALABLE :\n{quality_review}"
        f"{VERDICT_INSTRUCTION}"
    )


def feedback_quality(review: str) -> str:
    return (
        "Rédige un feedback correctif direct, concis et actionnable sous forme "
        "de liste à puces (bullet points) sans bavardage, à partir de cette review de qualité :\n\n"
        f"{review}"
    )


def feedback_security(review: str) -> str:
    return (
        "Rédige un feedback correctif de sécurité direct, concis et impératif sous "
        "forme de liste à puces (bullet points) à partir de cet audit de sécurité :\n\n"
        f"{review}"
    )


# ---------------------------------------------------------------------------
# Documentation et commit
# ---------------------------------------------------------------------------
def doc_in_repo(diff: str, max_chars: int) -> str:
    return (
        "Tu es un Technical Writer & Git Master. À partir de ce git diff définitivement validé dans le projet, génère :\n"
        "1. Une documentation technique concise des modifications apportées (fichiers touchés, comportement changé).\n"
        "2. Un message de commit Git conventionnel complet (type(scope): subject, corps explicatif).\n\n"
        f"GIT DIFF VALIDÉ :\n{truncate_middle(diff, max_chars)}"
    )


def doc_standalone(code: str, max_chars: int) -> str:
    return (
        "Tu es un Technical Writer & Git Master. À partir de ce code définitivement validé, "
        "génère :\n"
        "1. Une documentation technique concise (description, usage, prérequis).\n"
        "2. Un message de commit Git conventionnel complet (type(scope): subject, corps explicatif).\n\n"
        f"CODE VALIDÉ :\n{truncate_middle(code, max_chars)}"
    )


def doc_edit(diff: str, doc_text: str, max_chars: int) -> str:
    return (
        "Tu es un Technical Writer. Le code du projet vient d'être modifié et VALIDÉ ; ne le touche plus.\n"
        "Ta mission : mettre à jour la DOCUMENTATION EXISTANTE du projet pour refléter ces modifications.\n"
        "- Modifie uniquement des fichiers de documentation : README, CHANGELOG (ajoute une entrée en tête de la section "
        "« Non publié » ou équivalent s'il existe), fichiers *.md / *.rst / *.adoc et le dossier docs/.\n"
        "- Ne modifie AUCUN fichier de code, de configuration, de test ni de dépendances.\n"
        "- Sois concis et factuel ; ne documente que ce qui a réellement changé ; respecte le style et la langue existants.\n"
        "- Si aucune documentation n'est pertinente, ne modifie rien.\n\n"
        f"GIT DIFF VALIDÉ :\n{truncate_middle(diff, max_chars)}\n\n"
        f"NOTE DE DOCUMENTATION PROPOSÉE :\n{doc_text}"
    )

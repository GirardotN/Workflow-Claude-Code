"""
Politique d'outils donnée à l'agent Claude Code selon son rôle.

Principes :
- les agents qui relisent (qualité, sécurité, feedback, doc) n'ont AUCUN outil : ils ne voient que le
  texte du prompt (isolation cognitive réelle, pas seulement une consigne) ;
- l'agent de spécification lit le dépôt sans pouvoir le modifier ;
- l'agent de développement édite les fichiers ; Bash est optionnel (`--allow-bash`) et restreint à une
  liste blanche de commandes de test/lecture. Les commandes git qui déplacent HEAD ou l'index sont
  interdites : l'état Git appartient à l'orchestrateur (branche d'isolation, rollback, commit).

Ce n'est pas un bac à sable système : `Bash(pytest:*)` exécute le code du projet testé.
"""

from typing import List, Optional

from .roles import Role

READ_ONLY_TOOLS = "Read,Grep,Glob"
EDIT_TOOLS = "Read,Edit,Write,Grep,Glob"
NO_TOOLS = ""

# Commandes Bash autorisées pour l'agent de développement (syntaxe « préfixe:* » de Claude Code)
BASH_ALLOWED: List[str] = [
    "Bash(pytest:*)",
    "Bash(python -m pytest:*)",
    "Bash(python -m unittest:*)",
    "Bash(python3 -m pytest:*)",
    "Bash(python3 -m unittest:*)",
    "Bash(npm test:*)",
    "Bash(npm run test:*)",
    "Bash(npx jest:*)",
    "Bash(npx vitest:*)",
    "Bash(cargo test:*)",
    "Bash(cargo check:*)",
    "Bash(go test:*)",
    "Bash(dotnet test:*)",
    "Bash(dotnet build:*)",
    "Bash(git status:*)",
    "Bash(git diff:*)",
    "Bash(git log:*)",
    "Bash(git show:*)",
    "Bash(ls:*)",
    "Bash(cat:*)",
]

# Commandes interdites même si Bash est activé
BASH_DENIED: List[str] = [
    "Bash(git push:*)",
    "Bash(git commit:*)",
    "Bash(git checkout:*)",
    "Bash(git switch:*)",
    "Bash(git reset:*)",
    "Bash(git clean:*)",
    "Bash(git stash:*)",
    "Bash(git branch:*)",
    "Bash(git merge:*)",
    "Bash(git rebase:*)",
    "Bash(git restore:*)",
    "Bash(git add:*)",
    "Bash(rm:*)",
    "Bash(sudo:*)",
    "Bash(curl:*)",
    "Bash(wget:*)",
    "Bash(npm publish:*)",
    "Bash(pip install:*)",
    "Bash(npm install:*)",
]


class ToolPolicy:
    """Options d'outils d'un appel à Claude pour un rôle donné."""

    def __init__(
        self,
        tools: Optional[str],
        allowed_tools: Optional[List[str]] = None,
        disallowed_tools: Optional[List[str]] = None,
        permission_mode: Optional[str] = None,
    ):
        self.tools = tools
        self.allowed_tools = allowed_tools or []
        self.disallowed_tools = disallowed_tools or []
        self.permission_mode = permission_mode


def policy_for(role: Role, allow_bash: bool = False) -> ToolPolicy:
    """Retourne la politique d'outils associée à un rôle."""
    if role is Role.SPEC:
        return ToolPolicy(tools=READ_ONLY_TOOLS)
    if role is Role.DEV:
        if allow_bash:
            return ToolPolicy(
                tools=EDIT_TOOLS + ",Bash",
                allowed_tools=list(BASH_ALLOWED),
                disallowed_tools=list(BASH_DENIED),
                permission_mode="acceptEdits",
            )
        return ToolPolicy(tools=EDIT_TOOLS, permission_mode="acceptEdits")
    return ToolPolicy(tools=NO_TOOLS)

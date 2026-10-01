# Décisions d'architecture (ADR)

Un ADR consigne **une** décision structurante : le contexte, ce qui a été choisi, pourquoi, et ce que cela coûte. Ils ne sont pas réécrits : une décision remplacée est marquée « Remplacé par … » et un nouvel ADR est ajouté.

| N° | Titre | Statut |
| :--- | :--- | :--- |
| [0000](0000-spec-origine.md) | Spécification d'origine (document fondateur, historique) | Conservé tel quel |
| [0001](0001-claude-cli-subprocess.md) | Piloter Claude par le CLI `claude -p` (sous-processus) | Accepté |
| [0002](0002-role-de-jev.md) | Rôle de Jev : décider, en fail-closed | Accepté |
| [0003](0003-isolation-git-branche.md) | Isolation Git par branche + stash (et non par worktree) | Accepté |
| [0004](0004-garde-fou-agent-doc.md) | Agent de documentation encadré par un garde-fou a posteriori | Accepté |

## Modèle

```markdown
# ADR NNNN — Titre
- **Statut :** Proposé | Accepté | Remplacé par NNNN
- **Date :** AAAA-MM-JJ
## Contexte
## Décision
## Conséquences (positives, négatives, risques)
## Alternatives écartées
```

# Sécurité

## Signaler une vulnérabilité

Ne publiez pas de faille dans une issue publique. Utilisez l'onglet **Security → Report a vulnerability** du dépôt GitHub, ou écrivez au mainteneur (adresse dans `pyproject.toml`).

## Modèle de menace (état actuel, version 0.x)

`workflow` exécute un agent Claude Code qui **lit et modifie des fichiers** de votre projet, lance éventuellement des commandes, et envoie des extraits à un service tiers. Points d'attention :

| Risque | État actuel | Suite prévue |
| :--- | :--- | :--- |
| **Injection de prompt via le contenu du dépôt** (un fichier qui donne des ordres à l'agent) | Partiellement mitigé : l'agent de développement lit les fichiers du projet (inévitable) mais n'a pas Bash par défaut ; avec `--allow-bash`, liste blanche de commandes et git mutant interdit. Les relecteurs (qualité, sécurité, doc) n'ont **aucun outil** et tournent dans un répertoire vide : une injection dans un fichier ne peut pas les atteindre par lecture. | Durcissement de la liste blanche |
| **Données envoyées à TypeSafe (Jev)** : le diff et la revue sont transmis à `api.typesafe.ai` lors des validations | Secrets **masqués** (expressions régulières, « au mieux »), contenu plafonné ; `--jev-send review-only` n'envoie **aucun code** ; la clé TypeSafe n'apparaît jamais dans les erreurs/journaux/contextes | — |
| **Fiabilité des validations** | Fail-closed : réponse absente/invalide ou service en panne = arrêt du workflow, jamais « validé ». Sans clé TypeSafe : refus de démarrer. `--mock` : bandeau « SIMULATION », validations non fiables | — |
| **Secrets dans les prompts et rapports** : les prompts/sorties sont conservés dans `output/` | `output/` est ignoré par Git | Masquage, rapport JSON maîtrisé |
| **Perte de données locales** | Stash Guard + branche d'isolation + nettoyage garanti sur toute sortie (rollback, retour sur la branche d'origine, puis restauration du stash) ; testé sur dépôts réels. Limite : un conflit à la restauration du stash laisse vos modifications dans `git stash list` (jamais perdues). | Worktree dédié (ADR à venir) |
| **Clé API Anthropic** dans l'environnement : Claude Code pourrait l'utiliser au lieu de l'abonnement | `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL` et les variables Bedrock/Vertex/Foundry sont **retirées** de l'environnement du sous-processus Claude ; `--allow-api-key` pour les conserver (avertissement) | — |

Les limites ci-dessus sont connues et suivies ; ne faites pas confiance à l'outil sur un dépôt contenant des secrets ou du code propriétaire sans les avoir relues.

## Bonnes pratiques

- Lancez l'outil sur une **copie** ou une branche dédiée, avec un arbre de travail propre.
- Ne placez jamais de clé dans le dépôt : utilisez `.env` (ignoré par Git) ou `~/.config/workflow-claude/.env`.
- Relisez toujours le diff avant de fusionner la branche `workflow/ai-*`.

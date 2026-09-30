# Sécurité

## Signaler une vulnérabilité

Ne publiez pas de faille dans une issue publique. Utilisez l'onglet **Security → Report a vulnerability** du dépôt GitHub, ou écrivez au mainteneur (adresse dans `pyproject.toml`).

## Modèle de menace (état actuel, version 0.x)

`workflow` exécute un agent Claude Code qui **lit et modifie des fichiers** de votre projet, lance éventuellement des commandes, et envoie des extraits à un service tiers. Points d'attention :

| Risque | État actuel | Suite prévue |
| :--- | :--- | :--- |
| **Injection de prompt via le contenu du dépôt** (un fichier qui donne des ordres à l'agent) | Non mitigé : l'agent de développement lit les fichiers du projet. L'outil Bash est désactivé par défaut (`--allow-bash` pour l'activer). | Politique d'outils restreinte, revues sans accès aux fichiers |
| **Données envoyées à TypeSafe (Jev)** : le diff et la review sont transmis à `api.typesafe.ai` lors des validations | Envoi en clair du diff et de la review | Masquage des secrets, option `--jev-send=review-only` |
| **Secrets dans les prompts et rapports** : les prompts/sorties sont conservés dans `output/` | `output/` est ignoré par Git | Masquage, rapport JSON maîtrisé |
| **Perte de données locales** | Stash Guard + branche d'isolation ; des cas limites connus subsistent | Phase « sécurité Git » du plan de mise en conformité |
| **Clé API Anthropic** dans l'environnement : Claude Code pourrait l'utiliser au lieu de l'abonnement | Non filtrée | Nettoyage de l'environnement du sous-processus |

Les limites ci-dessus sont connues et suivies ; ne faites pas confiance à l'outil sur un dépôt contenant des secrets ou du code propriétaire sans les avoir relues.

## Bonnes pratiques

- Lancez l'outil sur une **copie** ou une branche dédiée, avec un arbre de travail propre.
- Ne placez jamais de clé dans le dépôt : utilisez `.env` (ignoré par Git) ou `~/.config/workflow-claude/.env`.
- Relisez toujours le diff avant de fusionner la branche `workflow/ai-*`.

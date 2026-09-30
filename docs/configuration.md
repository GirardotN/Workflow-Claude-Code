# Guide de Référence des Paramètres & Configuration

Ce document détaille l'ensemble des arguments en ligne de commande, des variables d'environnement et des fichiers de configuration supportés par **`Workflow-Claude-Code`**.

---

## Priorité de Résolution de la Configuration

L'orchestrateur applique un ordre de précédence strict :
```text
Ligne de Commande (CLI)
  └──> Préférences Utilisateur (~/.config/workflow-claude/config.json)
         └──> Variables d'Environnement du processus
                └──> Fichier .env du répertoire courant
                       └──> Fichier ~/.config/workflow-claude/.env
                              └──> Valeurs par Défaut du Code
```

---

## Options de la Ligne de Commande (`src/workflow_claude/cli.py`)

```text
usage: workflow [-h] [--mock] [--max-retries MAX_RETRIES] [--project-dir PROJECT_DIR]
                [--standalone] [--commit] [--branch] [--no-branch] [--merge]
                [--allow-bash] [--allow-api-key] [--jev-send {full,review-only}] [--doctor] [--run-tests] [--no-tests] [-y] [--workspace WORKSPACE] [-v]
                [prompt]
```

### Tableau Exhaustif des Arguments

| Option | Type | Valeur par défaut | Description & Cas d'Usage |
| :--- | :--- | :--- | :--- |
| `prompt` | `string` | *(Prompt démo)* | Demande exprimée en langage naturel décrivant la tâche de refactoring ou de développement. |
| `--project-dir` | `path` | `None` (auto `.`) | Répertoire du projet cible. Active automatiquement le mode **In-Repo** si le dossier est un dépôt Git valide. |
| `--standalone` | `flag` | `False` | Force la génération d'un fichier source unique et autonome dans `./output` sans altérer le projet hôte. |
| `--commit` | `flag` | `False` | Avec `--no-branch` : crée le commit conventionnel sur la branche active. **Sans effet avec l'isolation par branche** (défaut), où le succès est toujours committé sur `workflow/ai-*`. |
| `--branch` | `flag` | `True` | Isole le travail sur une branche dédiée `workflow/ai-<timestamp>` pour préserver la branche active. |
| `--no-branch` | `flag` | — | Désactive la création de branche d'isolation et applique les changements directement sur la branche active. |
| `--merge` | `flag` | `False` | Fusionne automatiquement la branche d'isolation dans la branche source en fin de cycle validé. |
| `--allow-bash` | `flag` | `False` | Autorise l'outil Bash pour l'agent de développement, **restreint à une liste blanche** (tests, lecture ; git modifiant l'état, `rm`, `curl`, `sudo` interdits — voir [under-the-hood](under-the-hood.md)). |
| `--allow-api-key` | `flag` | `False` | Laisse passer `ANTHROPIC_API_KEY` & co au CLI Claude (**facturation à l'usage**). Par défaut elles sont retirées pour n'utiliser que l'abonnement. |
| `--jev-send` | `full` \| `review-only` | `full` | Données envoyées au service tiers TypeSafe pour les validations (voir [SECURITY](../SECURITY.md)). |
| `--doctor` | `flag` | — | Vérifie l'environnement (git, CLI Claude, options, session, clés) sans consommer de quota, puis quitte (code 1 si point bloquant). |
| `--run-tests` | `flag` | `True` | Exécute automatiquement la suite de tests du projet hôte comme oracle de validation déterministe. |
| `--no-tests` | `flag` | — | Désactive l'exécution des tests du projet hôte. |
| `-y`, `--yes` | `flag` | `False` | Valide automatiquement les confirmations interactives (fusion de branche, retour sur branche source). |
| `--mock` | `flag` | `False` | Exécute la machine à états en simulation complète (zéro appel réseau TypeSafe et zéro appel CLI Claude). |
| `--max-retries` | `int` | `4` | Nombre maximum de cycles de correction en cas de rejet par les tests ou par l'audit (Circuit Breaker). |
| `--workspace` | `path` | `./output` | Répertoire où persister les artefacts générés (`LATEST_PATCH.diff`, documentation, rapport d'audit). |
| `-v`, `--verbose` | `flag` | `False` | Active les journaux d'exécution détaillés (niveau `DEBUG`) avec horodatage dans la console. |

---

## Variables d'Environnement (`.env`)

Copiez le fichier [.env.example](../.env.example) vers `.env` dans le **répertoire depuis lequel vous lancez `workflow`**, ou vers `~/.config/workflow-claude/.env` pour une configuration globale. Une variable déjà définie dans l'environnement du processus n'est jamais écrasée par un fichier `.env` :

| Variable | Type | Valeur par défaut | Rôle & Description |
| :--- | :--- | :--- | :--- |
| `TYPESAFE_API_KEY` | `string` | `""` | Clé API TypeSafe Jev (obtenue sur [typesafe.ai](https://typesafe.ai)). **Obligatoire** sauf avec `--mock` : sans elle, le programme refuse de démarrer (code 4). Jamais affichée ni journalisée, jamais renvoyée dans les prompts. |
| `TYPESAFE_API_URL` | `url` | `https://api.typesafe.ai/v1/systemone` | Endpoint TypeSafe System One (il n'existe pas d'endpoint `/v1/decide`). |
| `TYPESAFE_TIMEOUT_SECONDS`| `float` | `30.0` | Délai d'attente maximum pour les requêtes HTTP vers l'API TypeSafe. |
| `JEV_MAX_RETRIES` | `int` | `2` | Reprises (attente 1 s, 2 s ; `Retry-After` respecté, plafonné à 30 s) sur erreur réseau, 429 et 5xx. Jamais pour 400/401/403/422. Après épuisement : arrêt du workflow. |
| `JEV_THRESHOLD` | `float` | `0.5` | Seuil de validation : la probabilité `noul` (réponse « oui ») doit l'atteindre. |
| `JEV_MAX_STATE_CHARS` | `int` | `60000` | Plafond du texte envoyé à Jev (l'API refuse ~32 000 tokens). Au-delà, troncature au milieu du diff (la revue est conservée). |
| `JEV_SEND` | `full` ou `review-only` | `full` | `full` : diff (secrets masqués) + revue ; `review-only` : revue seule, aucun code transmis. Équivalent de `--jev-send`. |
| `CLAUDE_BIN` | `path` | Auto-détecté | Chemin absolu personnalisé vers le binaire `claude` (utile si non présent dans le `PATH`). |
| `CLAUDE_TIMEOUT_SECONDS` | `int` | `180` | Délai par défaut (revues, feedback, doc & commit). |
| `CLAUDE_TIMEOUT_SPEC_SECONDS` | `int` | `300` | Délai de l'étape d'exploration / spécification. |
| `CLAUDE_TIMEOUT_DEV_SECONDS` | `int` | `900` | Délai de l'agent de développement. |
| `CLAUDE_MAX_RETRIES` | `int` | `2` | Reprises sur erreur transitoire de Claude (surcharge serveur, 5xx). Jamais pour un quota ou une session expirée. |
| `ALLOW_API_KEY` | `0` ou `1` | `0` | Équivalent de `--allow-api-key`. |
| `MAX_WORKFLOW_RETRIES` | `int` | `4` | Nombre d'itérations autorisées avant déclenchement du Circuit Breaker. |
| `ALLOW_BASH` | `0` ou `1` | `0` | Autoriser ou interdire l'outil Bash pour l'agent de dev. |
| `USE_BRANCH` | `0` ou `1` | `1` | Isoler le travail sur une branche dédiée `workflow/ai-*`. |
| `RUN_TESTS` | `0` ou `1` | `1` | Exécuter la suite de tests du projet hôte comme oracle. |
| `MOCK_SERVICES` | `0` ou `1` | `0` | Forcer le mode simulation globale par défaut. |

---

## Codes de sortie

| Code | Signification |
| :--- | :--- |
| `0` | Succès |
| `1` | Erreur inattendue ou précondition non remplie (dépôt sans commit, identité Git absente, arbre non mis en réserve…) |
| `2` | Workflow terminé **sans succès** (circuit breaker, commit refusé par un hook…) |
| `3` | CLI Claude : session absente/expirée, limite d'usage atteinte, timeout, erreur |
| `4` | TypeSafe Jev : clé absente ou refusée, service indisponible, réponse invalide |
| `130` | Interruption (Ctrl-C) |

Dans tous les cas d'échec, le dépôt est remis dans son état d'origine et vos modifications en cours sont restaurées.

---

## Préférences Utilisateur Globales (`config.json`)

Pour définir des préférences qui s'appliquent à tous vos dépôts sans dupliquer de fichier `.env`, vous pouvez créer le fichier `~/.config/workflow-claude/config.json` :

```json
{
  "allow_bash": false,
  "run_tests": true,
  "use_branch": true
}
```

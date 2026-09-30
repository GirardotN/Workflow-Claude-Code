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
                [--allow-bash] [--run-tests] [--no-tests] [-y] [--workspace WORKSPACE] [-v]
                [prompt]
```

### Tableau Exhaustif des Arguments

| Option | Type | Valeur par défaut | Description & Cas d'Usage |
| :--- | :--- | :--- | :--- |
| `prompt` | `string` | *(Prompt démo)* | Demande exprimée en langage naturel décrivant la tâche de refactoring ou de développement. |
| `--project-dir` | `path` | `None` (auto `.`) | Répertoire du projet cible. Active automatiquement le mode **In-Repo** si le dossier est un dépôt Git valide. |
| `--standalone` | `flag` | `False` | Force la génération d'un fichier source unique et autonome dans `./output` sans altérer le projet hôte. |
| `--commit` | `flag` | `False` | Crée automatiquement le commit Git conventionnel une fois les modifications validées par l'audit. |
| `--branch` | `flag` | `True` | Isole le travail sur une branche dédiée `workflow/ai-<timestamp>` pour préserver la branche active. |
| `--no-branch` | `flag` | — | Désactive la création de branche d'isolation et applique les changements directement sur la branche active. |
| `--merge` | `flag` | `False` | Fusionne automatiquement la branche d'isolation dans la branche source en fin de cycle validé. |
| `--allow-bash` | `flag` | `False` | Autorise l'agent Claude à exécuter l'outil Bash *(désactivé par défaut pour des raisons de sécurité)*. |
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
| `TYPESAFE_API_KEY` | `string` | `""` | Clé API pour le moteur TypeSafe Jev (obtenue sur [typesafe.ai](https://typesafe.ai)). Si absente, bascule automatique sur le mock heuristique local. |
| `TYPESAFE_API_URL` | `url` | `https://api.typesafe.ai/v1/systemone` | Endpoint officiel TypeSafe System One. |
| `TYPESAFE_FALLBACK_URL` | `url` | `https://api.typesafe.ai/v1/decide` | Endpoint de secours pour l'API TypeSafe Decide directe. |
| `TYPESAFE_TIMEOUT_SECONDS`| `float` | `30.0` | Délai d'attente maximum pour les requêtes HTTP vers l'API TypeSafe. |
| `CLAUDE_BIN` | `path` | Auto-détecté | Chemin absolu personnalisé vers le binaire `claude` (utile si non présent dans le `PATH`). |
| `CLAUDE_TIMEOUT_SECONDS` | `int` | `180` | Délai d'expiration maximum par sous-processus Claude Code CLI. |
| `MAX_WORKFLOW_RETRIES` | `int` | `4` | Nombre d'itérations autorisées avant déclenchement du Circuit Breaker. |
| `ALLOW_BASH` | `0` ou `1` | `0` | Autoriser ou interdire l'outil Bash pour l'agent de dev. |
| `USE_BRANCH` | `0` ou `1` | `1` | Isoler le travail sur une branche dédiée `workflow/ai-*`. |
| `RUN_TESTS` | `0` ou `1` | `1` | Exécuter la suite de tests du projet hôte comme oracle. |
| `ALLOW_DIRTY` | `0` ou `1` | `0` | Autoriser l'exécution sans déclencher Stash Guard si la copie de travail a des modifications locales. |
| `MOCK_SERVICES` | `0` ou `1` | `0` | Forcer le mode simulation globale par défaut. |

---

## Préférences Utilisateur Globales (`config.json`)

Pour définir des préférences qui s'appliquent à tous vos dépôts sans dupliquer de fichier `.env`, vous pouvez créer le fichier `~/.config/workflow-claude/config.json` :

```json
{
  "allow_bash": false,
  "run_tests": true,
  "use_branch": true,
  "allow_dirty": false
}
```

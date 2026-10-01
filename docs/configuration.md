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
                [--allow-bash] [--allow-api-key] [--jev-send {full,review-only}] [--doctor] [--run-tests] [--no-tests] [--test-cmd COMMANDE] [--test-timeout SECONDES] [-y] [--workspace WORKSPACE] [-v]
                [prompt]
```

### Tableau Exhaustif des Arguments

| Option | Type | Valeur par défaut | Description & Cas d'Usage |
| :--- | :--- | :--- | :--- |
| `prompt` | `string` | *(obligatoire)* | Tâche en langage naturel. Obligatoire (sauf avec `--doctor` / `--version`) : `workflow` sans argument affiche l'aide d'usage au lieu de lancer un run. |
| `--project-dir` | `path` | `None` (auto `.`) | Répertoire du projet cible. Doit être un **dépôt Git** : un dossier introuvable ou non Git est une erreur (code 1) au lieu d'un passage silencieux en Standalone ; utilisez `--standalone` pour générer sans projet. Sans l'option : le dossier courant s'il est un dépôt Git, sinon Standalone (annoncé dans la bannière). |
| `--standalone` | `flag` | `False` | Force la génération d'un fichier source unique et autonome dans `./output` sans altérer le projet hôte. |
| `--commit` | `flag` | `False` | Avec `--no-branch` : crée le commit conventionnel sur la branche active. **Sans effet avec l'isolation par branche** (défaut), où le succès est toujours committé sur `workflow/ai-*`. |
| `--branch` | `flag` | `True` | Isole le travail sur une branche dédiée `workflow/ai-<timestamp>` pour préserver la branche active. |
| `--no-branch` | `flag` | — | Désactive la création de branche d'isolation et applique les changements directement sur la branche active. |
| `--merge` | `flag` | `False` | Fusionne automatiquement la branche d'isolation dans la branche source en fin de cycle validé. |
| `--allow-bash` / `--no-allow-bash` | `flag` | `False` | Autorise l'outil Bash pour l'agent de développement, **restreint à une liste blanche** (tests, lecture ; git modifiant l'état, `rm`, `curl`, `sudo` interdits — voir [under-the-hood](under-the-hood.md)). |
| `--allow-api-key` | `flag` | `False` | Laisse passer `ANTHROPIC_API_KEY` & co au CLI Claude (**facturation à l'usage**). Par défaut elles sont retirées pour n'utiliser que l'abonnement. |
| `--doc-edit` / `--no-doc-edit` | `flag` | `--doc-edit` | Mode In-Repo : agent de documentation du projet (README, CHANGELOG, docs/) dans le même commit ; toute modification hors documentation est annulée. |
| `--jev-send` | `full` \| `review-only` | `full` | Données envoyées au service tiers TypeSafe pour les validations (voir [SECURITY](../SECURITY.md)). |
| `--test-cmd` | `string` | *(auto)* | Commande de test du projet, remplace la détection automatique (sinon `TEST_COMMAND`, puis `.workflow.toml`). |
| `--test-timeout` | `int` | `300` | Délai maximum de la suite de tests ; l'arbre de processus est tué au-delà. |
| `--json` | `flag` | — | Sortie machine : le rapport JSON (sans contenu de prompts) est écrit sur **stdout**, tout l'affichage humain passe sur stderr. En cas d'erreur : `{"is_success": false, "error_message": ..., "exit_code": N}`. |
| `--log-file FICHIER` | `path` | — | Écrit aussi les logs détaillés (DEBUG) dans ce fichier. |
| `--no-color` | `flag` | — | Désactive couleurs et animations (la variable `NO_COLOR` est aussi respectée). |
| `--version` | `flag` | — | Affiche la version et quitte. |
| `--doctor` | `flag` | — | Vérifie l'environnement (git, CLI Claude, options, session, clés) sans consommer de quota, puis quitte (code 1 si point bloquant). |
| `--run-tests` / `--no-tests` (= `--no-run-tests`) | `flag` | `True` | Exécute automatiquement la suite de tests du projet hôte comme oracle de validation déterministe. |
| `--no-tests` | `flag` | — | Désactive l'exécution des tests du projet hôte. |
| `-y`, `--yes` | `flag` | `False` | Valide automatiquement les confirmations interactives (fusion de branche, retour sur branche source). |
| `--mock` | `flag` | `False` | Exécute la machine à états en simulation complète (zéro appel réseau TypeSafe et zéro appel CLI Claude). |
| `--max-retries` | `int` | `4` | Nombre maximum de cycles de correction en cas de rejet par les tests ou par l'audit (Circuit Breaker). |
| `--workspace` | `path` | `./output` | Répertoire des rapports, écrits **dans tous les cas (succès, échec, erreur, Ctrl-C)** : `WORKFLOW_AUDIT.md` (statut, tests, décisions Jev, étapes, coûts), `report.json`, `LATEST_PATCH.diff` (ou `FAILED_ATTEMPT.diff` / `rejected_solution.*` en cas d'échec), `DOC_CHANGES.diff`, `GENERATED_DOC.md`. S'il est dans le dépôt cible, il est exclu localement de git (`.git/info/exclude`). |
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
| `DOC_EDIT` | `0` ou `1` | `1` | Mode In-Repo : un agent met à jour la doc existante du projet (hors code, garde-fou). Équivalent de `--doc-edit` / `--no-doc-edit`. |
| `MAX_DIFF_CHARS` | `int` | `150000` | Plafond (caractères) du diff/code inséré dans les prompts des agents (troncature au milieu, signalée). |
| `TEST_COMMAND` | `string` | *(auto)* | Commande de test imposée à tous les projets (équivalent de `--test-cmd`). |
| `TEST_TIMEOUT_SECONDS` | `int` | `300` | Délai maximum de la suite de tests du projet. |
| `MAX_TEST_OUTPUT_CHARS` | `int` | `8000` | Taille maximale de la sortie de tests conservée (début et fin). |
| `ALLOW_API_KEY` | `0` ou `1` | `0` | Équivalent de `--allow-api-key`. |
| `MAX_WORKFLOW_RETRIES` | `int` | `4` | Nombre d'itérations autorisées avant déclenchement du Circuit Breaker. |
| `ALLOW_BASH` | `0` ou `1` | `0` | Autoriser ou interdire l'outil Bash pour l'agent de dev. |
| `USE_BRANCH` | `0` ou `1` | `1` | Isoler le travail sur une branche dédiée `workflow/ai-*`. |
| `RUN_TESTS` | `0` ou `1` | `1` | Exécuter la suite de tests du projet hôte comme oracle. |
| `MOCK_SERVICES` | `0` ou `1` | `0` | Forcer le mode simulation globale par défaut. |

---

## Configuration par projet (`.workflow.toml`)

Placez ce fichier à la racine du projet **cible** pour fixer sa commande de test :

```toml
[tests]
command = "pytest -x -q tests/unit"
timeout = 600
```

Priorité : `--test-cmd` > `TEST_COMMAND` > `.workflow.toml` > détection automatique.

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

Dans tous les cas d'échec, le dépôt est remis dans son état d'origine et vos modifications en cours sont restaurées. Les erreurs d'usage des arguments (prompt manquant, option invalide) renvoient `2`, comme le veut argparse.

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

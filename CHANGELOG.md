# Changelog

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/). Le projet suit le versionnement sémantique (`0.x` tant que l'API et la CLI ne sont pas stabilisées).

## [Non publié]

### Modifié
- **Fin de run In-Repo** : le succès est toujours committé sur la branche `workflow/ai-*` ; l'orchestrateur ramène ensuite l'utilisateur sur sa branche d'origine (fusion automatique avec `--merge`, sinon décision via callback/invite), puis restaure le stash **sur la branche d'origine** (avant, il l'était sur la branche de travail). `--commit` n'a d'effet qu'avec `--no-branch`.
- `GitClient` : toutes les commandes s'exécutent à la racine du dépôt ; `rollback` = `reset --hard` + `clean -fd` (lève une erreur au lieu de l'ignorer) ; `commit` lève `GitClientError` (hook, identité…) et passe le message sur stdin ; stash retrouvé par message (plus « le dernier ») ; `get_modified_files` fiable (`-z`, fichiers d'un dossier non suivi listés).
- Les fichiers non suivis créés par l'exécution des tests sont supprimés avant le diff/commit.
- Code déplacé dans le package `src/workflow_claude/` (plus de modules génériques `config`, `models`, `cli`, `orchestrator` installés à la racine de `site-packages`). Point d'entrée : `workflow_claude.cli:main` ; `python -m workflow_claude` disponible.
- Version ramenée à `0.1.0` (alpha), source unique dans `workflow_claude.__version__`.
- Configuration : le fichier `.env` est lu depuis le répertoire courant puis `~/.config/workflow-claude/.env` (et non plus à côté du module) ; les variables d'environnement déjà définies restent prioritaires. Les booléens de `config.json` et des variables d'environnement sont interprétés strictement (`"false"` n'est plus vrai).
- Spécification d'origine déplacée vers `docs/adr/0000-spec-origine.md`.

### Corrigé
- **Client Claude** : le prompt est envoyé sur **stdin** en octets exacts (plus de limite de ligne de commande Windows, plus d'injection via `cmd`, plus de LF → CRLF sous Windows). Sous Windows, le binaire natif `claude.exe` est appelé directement (sans `cmd.exe`).
- **« Zéro crédit API » enfin garanti** : `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, Bedrock/Vertex/Foundry sont retirées de l'environnement du sous-processus (sauf `--allow-api-key`).
- **Les erreurs Claude ne sont plus prises pour des résultats** : `is_error` du JSON est lu avant le code de sortie ; erreurs typées `ClaudeAuthError`, `ClaudeQuotaError`, `ClaudeTimeoutError`.
- **Isolation cognitive réelle** : les relecteurs (qualité, sécurité, feedback, doc) n'ont aucun outil et tournent dans un répertoire temporaire vide ; tout le mode Standalone aussi.
- **Circuit breaker décalé d'un cycle** : le dernier cycle autorisé n'était jamais revu (avec `--max-retries 1`, aucun succès possible). Il n'est plus évalué qu'à l'entrée d'un nouveau cycle DEV.
- **Stash restauré sur la mauvaise branche** et **aucun nettoyage sur exception/Ctrl-C/timeout** : `IsolatedRun` garantit rollback, retour sur la branche d'origine et `stash pop` sur toute sortie.
- Sujet de commit détecté par regex conventionnelle (plus de faux positifs type « feature request »), balises ``` jamais conservées.
- Dépôt sans commit, identité Git absente, HEAD détaché, branche déjà existante, projet dans un sous-dossier : gérés (erreur claire avant toute action, ou comportement correct).
- Suite de tests portable sous Windows/macOS (chemins temporaires résolus, `sys.executable`, configuration Git déterministe).
- `UnicodeEncodeError` du spinner quand stdout est redirigé en cp1252 sous Windows.
- `TestRunner.run_tests` : `custom_cmd` accepte une liste d'arguments ; découpage non-POSIX sous Windows.

### Ajouté
- `workflow --doctor` : diagnostic de l'environnement sans consommer de quota.
- `--allow-api-key` / `ALLOW_API_KEY`, délais par rôle (`CLAUDE_TIMEOUT_DEV_SECONDS` 900 s, `CLAUDE_TIMEOUT_SPEC_SECONDS` 300 s), reprises sur erreur transitoire (`CLAUDE_MAX_RETRIES`).
- `roles.py` (`Role`), `tool_policy.py` (outils par rôle, liste blanche/noire Bash pour `--allow-bash`), `doctor.py`.
- Tests : faux binaire `claude` (`tests/fake_claude.py`) pour tester le client de bout en bout sur les 3 OS.
- CI : job `lint` (ruff), test de fumée d'installation non éditable, `concurrency`, permissions en lecture seule.
- `.gitattributes`, `.editorconfig`, `.pre-commit-config.yaml`, `CONTRIBUTING.md`, `SECURITY.md`.

### Supprimé
- **`allow_dirty` / `ALLOW_DIRTY`** : incompatible avec le rollback (détruisait le travail de l'utilisateur). Un arbre de travail non propre est toujours mis en réserve ; si la mise en réserve échoue, le workflow s'arrête.
- Variable morte `DEFAULT_PROJECT_DIR`.

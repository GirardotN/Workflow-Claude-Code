# Changelog

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/). Le projet suit le versionnement sémantique (`0.x` tant que l'API et la CLI ne sont pas stabilisées).

## [Non publié]

### Modifié
- **Un seul moteur** (`fsm.WorkflowEngine`) pour les modes In-Repo et Standalone : ~500 lignes dupliquées supprimées, `orchestrator.py` ne garde que les services. Comportement et noms d'étapes inchangés.
- **`--mock` ne modifie plus jamais le code d'un projet** : l'agent de dev simulé n'écrit que `WORKFLOW_MOCK.md`. La simulation est indexée par rôle (l'ancienne, par sous-chaîne du prompt, ré-appliquait l'édition lors de la revue d'un diff contenant « in-situ »).
- Replis silencieux du routage (`from_str`) désormais journalisés ; champ mort `target_files` supprimé.
- **Fin de run In-Repo** : le succès est toujours committé sur la branche `workflow/ai-*` ; l'orchestrateur ramène ensuite l'utilisateur sur sa branche d'origine (fusion automatique avec `--merge`, sinon décision via callback/invite), puis restaure le stash **sur la branche d'origine** (avant, il l'était sur la branche de travail). `--commit` n'a d'effet qu'avec `--no-branch`.
- `GitClient` : toutes les commandes s'exécutent à la racine du dépôt ; `rollback` = `reset --hard` + `clean -fd` (lève une erreur au lieu de l'ignorer) ; `commit` lève `GitClientError` (hook, identité…) et passe le message sur stdin ; stash retrouvé par message (plus « le dernier ») ; `get_modified_files` fiable (`-z`, fichiers d'un dossier non suivi listés).
- Les fichiers non suivis créés par l'exécution des tests sont supprimés avant le diff/commit.
- Code déplacé dans le package `src/workflow_claude/` (plus de modules génériques `config`, `models`, `cli`, `orchestrator` installés à la racine de `site-packages`). Point d'entrée : `workflow_claude.cli:main` ; `python -m workflow_claude` disponible.
- Version ramenée à `0.1.0` (alpha), source unique dans `workflow_claude.__version__`.
- Configuration : le fichier `.env` est lu depuis le répertoire courant puis `~/.config/workflow-claude/.env` (et non plus à côté du module) ; les variables d'environnement déjà définies restent prioritaires. Les booléens de `config.json` et des variables d'environnement sont interprétés strictement (`"false"` n'est plus vrai).
- Spécification d'origine déplacée vers `docs/adr/0000-spec-origine.md`.

### Corrigé
- **`workflow` sans argument lançait un vrai run** avec un prompt de démonstration : le prompt est maintenant obligatoire (aide d'usage sinon).
- **`--project-dir` introuvable ou non Git passait silencieusement en Standalone** : erreur explicite (code 1) ; la bannière annonce le mode réellement utilisé quand l'option est absente.
- **Les rapports sont écrits dans tous les cas** (succès, échec, exception, Ctrl-C) et APRÈS la remise en état du dépôt : avant, rien n'était écrit en cas d'échec alors que la doc y renvoyait ; un dossier de rapports situé dans le dépôt pouvait être effacé par le rollback (`git clean`) ou polluer `git status` : il est maintenant exclu localement (`.git/info/exclude`).
- **Baseline des tests fiable** : la régression est détectée par comparaison des **ensembles de tests en échec** (extraits de pytest, unittest, jest, vitest, go, cargo, dotnet) et non plus du texte brut de la sortie (sensible aux temps, adresses mémoire, chemins temporaires).
- Le timeout des tests tue **tout l'arbre de processus** (avant : seul le parent, les enfants survivaient) ; stdin fermé et `CI=true` : plus de test bloqué en mode « watch ».
- Sous Windows, une commande de test donnée en chaîne conservait ses guillemets et ne se lançait pas ; le raccourci `python3` du Microsoft Store n'est plus utilisé.
- Circuit breaker : le message mentionne les tests encore en échec au dernier cycle.
- **Jev : toutes les validations passaient.** Le client lisait `results.<clé>.probability` alors que l'API renvoie `answers.<clé>.noul` ; la valeur par défaut (1.0) validait tout, et le repli sur `/v1/decide` (endpoint inexistant, 404) ne fonctionnait jamais. Le client suit maintenant le format réel, vérifié contre le service.
- **Fail-closed** : réponse absente/invalide ou service en panne → `JevApiError` (code de sortie 4), jamais de validation par défaut ; reprises sur erreur réseau/429/5xx.
- Sans clé TypeSafe, le client ne bascule plus silencieusement en simulation : refus de démarrer (sauf `--mock`).
- Le code de sortie du CLI reflète le résultat : `2` si le workflow est incomplet (avant : toujours `0`), `3` Claude, `4` Jev, `130` Ctrl-C.
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
- CLI : `--version`, `--json` (rapport JSON sur stdout, affichage humain sur stderr), `--log-file`, `--no-color` (+ `NO_COLOR`), formes `--no-branch` / `--no-allow-bash` / `--no-run-tests` / `--no-doc-edit` (`BooleanOptionalAction`) ; activation des séquences ANSI de la console Windows.
- `report.json` (durées, modèles, coûts, décisions Jev, tests ; sans contenu de prompts) et `WORKFLOW_AUDIT.md` enrichi (statut, tests baseline/dernier cycle, décisions Jev, coût par étape, branche, fusion, alerte de stash non restauré) ; `WorkflowExecutionReport.to_dict()`.
- `--test-cmd` / `TEST_COMMAND`, `--test-timeout` / `TEST_TIMEOUT_SECONDS`, `MAX_TEST_OUTPUT_CHARS` et fichier `.workflow.toml` par projet (`[tests] command`, `timeout`).
- Détection des tests : pnpm / yarn / bun (lockfile), venv Python du projet, .NET, Maven, Gradle (+ wrappers).
- `report.baseline_tests_failed` et `report.tests_failed`.
- **Agent doc (`DOC_EDIT`, mode In-Repo)** : met à jour la documentation existante du projet (README, CHANGELOG, `*.md`/`*.rst`/`*.adoc`, `docs/`) dans le même commit que le code. `doc_guard.py` annule ensuite toute modification hors documentation (code, config, tests, suppressions) et protège le code validé ; une panne de l'agent ne remet jamais en cause le code validé. `--no-doc-edit` / `DOC_EDIT=0` pour le couper ; `DOC_CHANGES.diff` séparé du patch de code.
- `policy.py` (`ModelPolicy`, matrice des modèles, testée contre le tableau de `docs/architecture.md`), `prompts.py` (tous les gabarits + consignes par spécialité via `--append-system-prompt`), `fsm.py` (moteur unique + backends), `clients/mocks.py`, `text_utils.py`.
- Contrôle de contexte : diff relu sans lockfiles/fichiers générés (`package-lock.json`, `dist/`, `*.min.js`…, avec mention des fichiers exclus), plafond `MAX_DIFF_CHARS` (150 000) dans les prompts, coût/tours/session de chaque appel Claude dans les métadonnées des étapes (`report.cost_usd`).
- `jev_context.py` : masquage des secrets et plafonnement du texte envoyé à Jev ; `--jev-send {full,review-only}` / `JEV_SEND`.
- Trace des décisions Jev dans le rapport (`report.decisions`, `report.jev_mode`) et dans le résumé du CLI ; contrôle de cohérence avec le `VERDICT: PASS|FAIL` des relecteurs.
- Descriptions des niveaux de complexité et des spécialités transmises à Jev pour le routage.
- `workflow --doctor` teste la clé TypeSafe (ping minimal) ; `JEV_MAX_RETRIES`, `JEV_THRESHOLD`, `JEV_MAX_STATE_CHARS`.
- Tests : faux serveur TypeSafe au format réel (`tests/fake_jev.py`), tests du CLI et des codes de sortie.
- `workflow --doctor` : diagnostic de l'environnement sans consommer de quota.
- `--allow-api-key` / `ALLOW_API_KEY`, délais par rôle (`CLAUDE_TIMEOUT_DEV_SECONDS` 900 s, `CLAUDE_TIMEOUT_SPEC_SECONDS` 300 s), reprises sur erreur transitoire (`CLAUDE_MAX_RETRIES`).
- `roles.py` (`Role`), `tool_policy.py` (outils par rôle, liste blanche/noire Bash pour `--allow-bash`), `doctor.py`.
- Tests : faux binaire `claude` (`tests/fake_claude.py`) pour tester le client de bout en bout sur les 3 OS.
- CI : job `lint` (ruff), test de fumée d'installation non éditable, `concurrency`, permissions en lecture seule.
- `.gitattributes`, `.editorconfig`, `.pre-commit-config.yaml`, `CONTRIBUTING.md`, `SECURITY.md`.

### Supprimé
- `TYPESAFE_FALLBACK_URL` et le repli `/v1/decide` (endpoint inexistant).
- **`allow_dirty` / `ALLOW_DIRTY`** : incompatible avec le rollback (détruisait le travail de l'utilisateur). Un arbre de travail non propre est toujours mis en réserve ; si la mise en réserve échoue, le workflow s'arrête.
- Variable morte `DEFAULT_PROJECT_DIR`.

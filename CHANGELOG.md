# Changelog

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/). Le projet suit le versionnement sémantique (`0.x` tant que l'API et la CLI ne sont pas stabilisées).

## [Non publié]

### Modifié
- Code déplacé dans le package `src/workflow_claude/` (plus de modules génériques `config`, `models`, `cli`, `orchestrator` installés à la racine de `site-packages`). Point d'entrée : `workflow_claude.cli:main` ; `python -m workflow_claude` disponible.
- Version ramenée à `0.1.0` (alpha), source unique dans `workflow_claude.__version__`.
- Configuration : le fichier `.env` est lu depuis le répertoire courant puis `~/.config/workflow-claude/.env` (et non plus à côté du module) ; les variables d'environnement déjà définies restent prioritaires. Les booléens de `config.json` et des variables d'environnement sont interprétés strictement (`"false"` n'est plus vrai).
- Spécification d'origine déplacée vers `docs/adr/0000-spec-origine.md`.

### Corrigé
- Suite de tests portable sous Windows/macOS (chemins temporaires résolus, `sys.executable`, configuration Git déterministe).
- `UnicodeEncodeError` du spinner quand stdout est redirigé en cp1252 sous Windows.
- `TestRunner.run_tests` : `custom_cmd` accepte une liste d'arguments ; découpage non-POSIX sous Windows.

### Ajouté
- CI : job `lint` (ruff), test de fumée d'installation non éditable, `concurrency`, permissions en lecture seule.
- `.gitattributes`, `.editorconfig`, `.pre-commit-config.yaml`, `CONTRIBUTING.md`, `SECURITY.md`.

### Supprimé
- Variable morte `DEFAULT_PROJECT_DIR`.

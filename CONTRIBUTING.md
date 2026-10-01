# Contribuer

## Mise en place de l'environnement

Prérequis : Python 3.10+, Git, et (pour les essais réels) le CLI Claude Code authentifié.

```bash
git clone https://github.com/GirardotN/Workflow-Claude-Code.git
cd Workflow-Claude-Code
python -m venv .venv
# Linux/macOS : source .venv/bin/activate     Windows : .venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install
```

L'installation éditable est **obligatoire** : les tests importent le package `workflow_claude` (dossier `src/`).

## Commandes utiles

```bash
python -m unittest discover -s tests -v   # suite de tests (hermétique, ~400 tests, 2-3 minutes)
coverage run -m unittest discover -s tests && coverage report   # couverture (seuil CI : 90 %)
ruff check .                              # lint (même commande qu'en CI)
ruff check --fix .                        # corrige notamment le tri des imports
mypy                                      # typage (informatif pour l'instant)
workflow --mock --standalone "test"       # essai de bout en bout sans réseau ni Claude
```

## Organisation du code

```
src/workflow_claude/
  cli.py            point d'entrée (argparse, affichage, codes de sortie)
  config.py         variables d'environnement, .env, config.json
  models.py         énumérations et rapport d'exécution
  orchestrator.py   machine à états (modes In-Repo et Standalone)
  clients/          claude_cli, jev_client, git_client, test_runner
  ui/               spinner, diff coloré, confirmations
tests/              unittest, tout en mock
docs/               guides et ADR (docs/adr/)
```

## Conventions

- Tests : voir [docs/testing.md](docs/testing.md) (doubles de test, conventions, vérification par mutation).
- Code, docstrings, logs et prompts en **français** ; identifiants existants conservés.
- Imports **relatifs** à l'intérieur du package (`from .clients.git_client import GitClient`) ; les tests importent `workflow_claude.…`.
- Aucun appel réseau ni CLI `claude` dans les tests : utiliser `mock_mode=True` ou des doubles.
- Les tests qui créent des dépôts Git doivent résoudre les chemins temporaires (`Path(...).resolve()`) et configurer `user.name`, `user.email`, `core.autocrlf=false`, `commit.gpgsign=false` localement.
- Les noms d'étapes (`DEV_IN_SITU_CYCLE_n`, `CHECK_QUALITE_*`…) sont un contrat implicite utilisé par les tests et par `cli.print_step` : ne pas les renommer sans mettre à jour les deux.
- Messages de commit : [Conventional Commits](https://www.conventionalcommits.org/fr/) (`feat(scope): …`, `fix: …`, `docs: …`).
- Une évolution visible par l'utilisateur ajoute une ligne dans `CHANGELOG.md` (section « Non publié »).

## Pull requests

La CI (`.github/workflows/ci.yml`) doit être verte : lint, tests sur Linux/macOS/Windows × Python 3.10–3.12, et test de fumée d'installation.

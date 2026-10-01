# Tests : organisation, outils et conventions

Le projet compte environ 400 tests `unittest`, **hermétiques** : aucun appel réseau, aucun vrai `claude`, aucune clé nécessaire. La couverture mesurée est d'environ **96 %** ; la CI échoue sous **90 %** (`[tool.coverage.report] fail_under` dans `pyproject.toml`).

## Lancer les tests

```bash
pip install -e ".[dev]"                          # installation éditable obligatoire (le code vit dans src/)
python -m unittest discover -s tests             # tout (≈ 2-3 minutes : vrais dépôts Git et vrais processus)
python -m unittest discover -s tests -p "test_isolation.py"   # un seul fichier
coverage run -m unittest discover -s tests && coverage report # couverture (même commande qu'en CI)
ruff check .                                     # lint
```

## Les doubles de test : ce qui est « réel » et ce qui ne l'est pas

| Brique | Réel | Simulé |
| :--- | :--- | :--- |
| **Git** | Vrais dépôts temporaires (`tests/gitrepo.py`), vraies branches, stash, merge, hooks, sous-modules | — |
| **Processus de test du projet cible** | Vrais sous-processus (`sys.executable -c ...`), vrai timeout, vrai arbre de processus tué | — |
| **CLI `claude`** | Appels de sous-processus, stdin, environnement, JSON, codes de sortie | `tests/fake_claude.py` : faux binaire piloté par `FAKE_CLAUDE_MODE` (`ok`, `error`, `flaky`, `sleep`, `plain`, `denials`...) ; il enregistre arguments, taille/empreinte du prompt, `cwd` et variables sensibles |
| **API TypeSafe Jev** | Vraie requête HTTP vers un serveur local, mêmes schémas de réponse que le service réel | `tests/fake_jev.py` : serveur `http.server` rejouant des « scénarios » (`ok_noul`, `ok_choice`, `error(...)`, `sleep`) |
| **Agents (Claude) dans les workflows** | Moteur FSM, isolation Git, garde-fous | `ClaudeCliClient(mock_mode=True)` → `clients/mocks.py` (réponses par **rôle**) |

Les vrais services n'ont été sondés qu'à la main (clé TypeSafe réelle, CLI `claude` réel pour `--help`/`auth status`) pour **établir** les formats que les doubles reproduisent. Un run complet avec un Claude connecté reste à faire manuellement (voir le README).

## Fichiers de tests

| Fichier | Couvre |
| :--- | :--- |
| `test_isolation.py`, `test_git_client.py`, `test_edge_cases.py` | Sécurité des données : Stash Guard, branche d'isolation, nettoyage sur toute sortie (erreur, Ctrl-C), rollback, commit/hook, fusion, HEAD détaché, sous-modules, branche qui avance pendant le run (fusion sans conflit et en conflit) |
| `test_claude_cli.py`, `test_tool_policy.py` | Client Claude : prompt sur stdin (240 000 caractères), environnement nettoyé, erreurs typées, reprises, délais, politique d'outils par rôle, isolation cognitive |
| `test_jev_client.py`, `test_jev_context.py`, `test_orchestrator_jev.py` | Jev : format réel, **fail-closed**, reprises, plafond de taille, masquage des secrets, traçabilité, cohérence avec le verdict des relecteurs |
| `test_engine.py`, `test_orchestrator.py`, `test_in_repo.py`, `test_policy_and_prompts.py` | Moteur FSM unique : mêmes étapes pour les deux modes, matrice des modèles **comparée à `docs/architecture.md`**, contrats des prompts |
| `test_doc_edit.py` | Agent de documentation et `doc_guard` (tout ce qui n'est pas de la doc est annulé) |
| `test_oracle.py`, `test_test_runner.py` | Oracle de tests : détection, `.workflow.toml`, timeout qui tue l'arbre, baseline par ensembles d'échecs |
| `test_cli.py`, `test_cli_ux.py`, `test_doctor.py`, `test_mocks.py` | CLI : codes de sortie, `--json`, `--log-file`, rapports en toutes circonstances, `--doctor`, `--mock` sans risque |
| `test_infrastructure.py` | Configuration, terminal, diagnostics, cas d'erreur Git, chemins d'échec de l'isolation, résumé et audit |

## Conventions

- **Dépôts Git de test** : toujours via `gitrepo.make_repo()` (chemins résolus — `/private/var` sous macOS, noms longs sous Windows —, `core.autocrlf=false`, identité locale). Ne jamais lancer un test sur le vrai dépôt : le CLI se teste avec `--standalone` ou un dépôt temporaire.
- **Aucun réseau** : Jev → `fake_jev.py` ou `mock_mode=True` ; Claude → `fake_claude.py` ou `mock_mode=True`. `JevClient()` sans clé lève `JevConfigError` : toujours passer un client explicite.
- **Comparer des chemins** après `.resolve()`. Sous Windows, ne jamais supposer `python3` : utiliser `sys.executable`.
- **Noms d'étapes** (`DEV_IN_SITU_CYCLE_n`, `CHECK_QUALITE_*`...) : contrat implicite vérifié par `test_engine.py`.
- **Rapport simulé** : utiliser un vrai `WorkflowExecutionReport`, pas un `Mock` (le résumé du CLI lit beaucoup de champs).

## Vérification par mutation (pratique recommandée)

Pour chaque correctif important, vérifier que le test **échoue sans le correctif** : committer, casser temporairement la ligne concernée (par exemple remettre l'ancien circuit breaker, retirer le nettoyage d'environnement, désactiver l'annulation de `doc_guard`), lancer le fichier de tests, puis restaurer **ce seul fichier** avec `git checkout -- <fichier>`. Cette méthode a déjà montré que les tests détectent : l'ancien circuit breaker, l'ancien rollback, l'absence de masquage des secrets, la validation par défaut de Jev, l'environnement non nettoyé, la comparaison de texte des tests, le timeout qui ne tue que le parent.

## CI (`.github/workflows/ci.yml`)

- **Tests** : Ubuntu, macOS, Windows × Python 3.10, 3.11, 3.12.
- **Lint** : `ruff`.
- **Installation** : `pip install .` non éditable puis `workflow --version` et un run `--mock` hors du dépôt, sur Ubuntu et Windows ; installation `pipx` sur Ubuntu.
- **Couverture** : Ubuntu / Python 3.12, seuil 90 %.

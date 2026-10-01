# Prise en main : votre premier run en 10 minutes

Ce guide suppose que vous ne connaissez pas le projet. À la fin, vous aurez lancé `workflow` sur un petit dépôt jetable, lu son rapport, et vous saurez comment accepter ou rejeter son travail. Comptez 10 minutes et un tout petit peu de votre quota Claude.

## Ce que fait l'outil, en deux phrases

Vous décrivez une tâche en français (« ajoute une fonction `add` »). `workflow` la confie à plusieurs agents Claude qui travaillent **sur une branche séparée de votre dépôt Git** : un agent modifie le code, vos tests tournent, deux autres agents relisent le résultat, un service de décision (Jev) dit oui ou non, puis le travail est committé. **Votre branche et vos modifications en cours ne sont jamais touchées sans votre accord.**

Les mots utilisés par l'outil sont expliqués dans le [glossaire](glossaire.md).

## Étape 0 : ce qu'il vous faut

| Quoi | Comment | Vérifier |
| :--- | :--- | :--- |
| **Git** avec votre identité | `git config --global user.name "Votre Nom"` et `git config --global user.email "vous@exemple.com"` | `git --version` |
| **Python 3.10+** | python.org, `winget install Python.Python.3.12` (Windows), `brew install python` (macOS) | `python --version` |
| **Node.js 18+** et le **CLI Claude Code** | `npm install -g @anthropic-ai/claude-code` | `claude --version` |
| Un **abonnement Claude** (Pro ou Max) connecté au CLI | `claude auth login` (ouvre le navigateur) | `claude auth status` doit afficher `"loggedIn": true` |
| Une **clé TypeSafe** (service Jev) | Créez un compte sur [typesafe.ai](https://typesafe.ai) et générez une clé API | voir étape 2 |

> Sous Windows, si `claude` n'est pas reconnu juste après l'installation, fermez et rouvrez le terminal. Détails : [windows.md](windows.md).

## Étape 1 : installer `workflow`

```bash
git clone https://github.com/GirardotN/Workflow-Claude-Code.git
cd Workflow-Claude-Code
pipx install .
workflow --version        # affiche : workflow 0.1.0
```

Pas de `pipx` ? `python -m pip install --user pipx && python -m pipx ensurepath`, puis rouvrez le terminal.

## Étape 2 : configurer la clé TypeSafe

Créez le fichier de configuration **global** (valable quel que soit le dossier d'où vous lancez `workflow`) :

```bash
# Linux / macOS
mkdir -p ~/.config/workflow-claude
cp .env.example ~/.config/workflow-claude/.env
```
```powershell
# Windows (PowerShell)
New-Item -ItemType Directory -Force "$env:USERPROFILE\.config\workflow-claude" | Out-Null
Copy-Item .env.example "$env:USERPROFILE\.config\workflow-claude\.env"
```

Ouvrez ce fichier et remplacez la ligne `TYPESAFE_API_KEY=votre_cle_typesafe_ici` par votre vraie clé. Ce fichier est **local** : il n'est jamais envoyé sur GitHub. (Alternative : un fichier `.env` dans le dossier où vous lancez la commande, ou la variable d'environnement `TYPESAFE_API_KEY`.)

## Étape 3 : vérifier que tout est prêt

```bash
workflow --doctor
```

Résultat attendu (n'utilise **aucun** quota Claude) :

```
✅ Python                       3.12.10
✅ Git                          git version 2.55.0
✅ Identité Git                 Votre Nom <vous@exemple.com>
✅ Claude Code CLI              2.1.286 (Claude Code)
✅ Options du CLI               toutes les options requises sont disponibles
✅ Session Claude               connecté (claude.ai)
✅ Facturation à l'usage        aucune clé API payante dans l'environnement
✅ TypeSafe Jev                 clé valide, service joignable (1205 ms)
✅ Environnement utilisable (0 avertissement(s)).
```

Une ligne ❌ vous dit exactement quoi corriger. Si vous voyez `ANTHROPIC_API_KEY présent … retiré du sous-processus Claude`, c'est normal : l'outil retire ces variables pour n'utiliser que votre abonnement.

## Étape 4 : créer un dépôt jetable

```bash
mkdir demo-calc && cd demo-calc
git init -b main
mkdir tests && touch tests/__init__.py
printf '"""Petite calculatrice."""\n\n\ndef subtract(a, b):\n    """Retourne a - b."""\n    return a - b\n' > calc.py
printf 'import unittest\n\nfrom calc import subtract\n\n\nclass TestCalc(unittest.TestCase):\n    def test_subtract(self):\n        self.assertEqual(subtract(5, 3), 2)\n' > tests/test_calc.py
printf '# Calc\n\nPetite calculatrice de démonstration.\n' > README.md
git add -A && git commit -m "chore: initial commit"
python -m unittest discover -s tests      # 1 test, OK
```

(Sous PowerShell, créez simplement ces trois fichiers avec un éditeur : `calc.py`, `tests/__init__.py` vide, `tests/test_calc.py`.)

## Étape 5 : lancer votre premier run

Depuis le dossier `demo-calc` :

```bash
workflow --project-dir . "Ajoute une fonction add(a, b) dans calc.py qui retourne la somme de a et b, avec un test unitaire dans tests/test_calc.py"
```

Cela dure environ une minute. Voici ce qui s'affiche, abrégé (c'est une vraie exécution) :

```
• Mode de travail  : In-Repo : .            ← vos fichiers sont modifiés, sur une branche à part
• TypeSafe Jev     : API réelle (clé configurée)
  ⏳ Vérification de la santé initiale des tests (Baseline)...   ← vos tests avant toute modification
  🔍 [1_EXPLORATION_ET_SPEC]  Modèle: sonnet (16.48s)            ← un agent lit le code et écrit un plan
  💻 [DEV_IN_SITU_CYCLE_1]    Modèle: sonnet (14.05s)            ← un agent modifie les fichiers
  🧪✅ [TESTS_PASSED_CYCLE_1]                                    ← vos tests passent toujours
  🛡️ [CHECK_QUALITE_DIFF_CYCLE_1] Modèle: sonnet (7.18s)         ← un 2e agent relit le diff
  >>> Jev : VALIDATION Qualité accordée.                         ← le service de décision dit oui
  📦 [FINAL_DOC_ET_COMMIT]    📚 [DOC_EDIT]                      ← message de commit + doc du projet

Voulez-vous fusionner 'workflow/ai-1790853674' dans 'main' ? [Y/n] :
```

Répondez **`n`** pour cette première fois : le travail reste sur sa branche et vous pouvez l'examiner tranquillement.

## Étape 6 : examiner le résultat

```bash
git branch                         # vous êtes toujours sur main ; la branche workflow/ai-... existe
git log --oneline main..workflow/ai-1790853674
git diff main...workflow/ai-1790853674     # tout ce que l'agent a changé
```

Vous devez voir un commit `feat(calc): ajouter la fonction add` qui ajoute `add` dans `calc.py` et un test dans `tests/test_calc.py`, rien d'autre.

**Accepter** le travail :
```bash
git merge workflow/ai-1790853674
```
**Rejeter** le travail :
```bash
git branch -D workflow/ai-1790853674
```

Dans les deux cas, `main` n'a changé que si vous l'avez décidé.

## Étape 7 : lire le rapport

Un dossier `output/` a été créé (il est exclu de git automatiquement) :

| Fichier | Contenu |
| :--- | :--- |
| `WORKFLOW_AUDIT.md` | Résumé lisible : statut, type de tâche, tests avant/après, **décisions de Jev avec leur probabilité**, durée et coût de chaque étape |
| `report.json` | Même information, pour un traitement automatique |
| `LATEST_PATCH.diff` | Le patch validé |
| `GENERATED_DOC.md` | La documentation et le message de commit proposés |

Extrait réel de `WORKFLOW_AUDIT.md` :

```
- Statut : SUCCÈS
- Type de Workflow : Tâche Simple · Spécialité : Dev Python · Itérations : 1
- Tests (baseline) : verts · Tests (dernier cycle) : verts
## Décisions Jev :
- ROUTAGE_COMPLEXITE : Tâche Simple (1.00), 594 ms
- VALIDATION_QUALITE_CYCLE_1 : True (0.79), 306 ms
```

(`0.79` = probabilité que le code soit valide selon Jev ; il faut au moins `0.5`.)

## Et ensuite ?

- **Sur votre vrai projet** : commencez par une petite tâche, avec un arbre de travail propre ou non (vos modifications en cours sont mises à l'abri puis restaurées) :
  `workflow --project-dir /chemin/du/projet "Votre tâche"`.
- **Quand ça échoue** : le dépôt est remis dans son état d'origine ; lisez `output/WORKFLOW_AUDIT.md` et la page [Dépannage](troubleshooting.md). Les codes de sortie (0 succès, 2 workflow incomplet, 3 problème Claude, 4 problème Jev) sont décrits dans le [README](../README.md#codes-de-sortie).
- **Pour tester sans rien risquer ni consommer** : `workflow --mock --standalone "essai"` (simulation : les validations ne sont **pas** fiables).
- **Pour ne pas envoyer de code à TypeSafe** : ajoutez `--jev-send review-only`.
- **Pour aller plus loin** : [Cookbooks](cookbooks.md) (scénarios), [Configuration](configuration.md) (toutes les options), [Architecture](architecture.md) (comment ça marche).

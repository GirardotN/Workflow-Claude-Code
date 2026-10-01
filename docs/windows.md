# Windows : installation et particularités

## Prérequis

| Outil | Installation conseillée | Remarque |
| :--- | :--- | :--- |
| **Git for Windows** | `winget install Git.Git` | Configurez l'identité : `git config --global user.name "Nom"` et `git config --global user.email "mail"` |
| **Python 3.10+** | `winget install Python.Python.3.12` ou python.org | Évitez le raccourci `python3` du **Microsoft Store** (dossier `WindowsApps`) : il ne lance rien et l'outil l'ignore |
| **Node.js 18+** | `winget install OpenJS.NodeJS.LTS` | Nécessaire pour installer le CLI Claude |
| **CLI Claude Code** | `npm install -g @anthropic-ai/claude-code` puis `claude auth login` | Fermez et rouvrez le terminal après l'installation pour que le PATH soit à jour |
| **pipx** (facultatif) | `python -m pip install --user pipx` puis `python -m pipx ensurepath` | Pour installer la commande `workflow` globalement |

Vérification : `workflow --doctor` (n'utilise aucun quota Claude).

## Ce qui est spécifique à Windows (et déjà géré)

- **Pas de `cmd.exe` pour lancer Claude.** Le shim npm `claude.cmd` ne fait que lancer `node_modules\@anthropic-ai\claude-code\bin\claude.exe` : `workflow` appelle directement ce binaire natif. Le prompt est transmis sur l'entrée standard, donc il n'y a ni limite de longueur de ligne de commande, ni interprétation de `& | % ^ "`.
- **Encodage** : la sortie est reconfigurée en UTF-8 ; un flux redirigé qui ne sait pas encoder les emojis (cp1252) ne fait plus planter l'affichage.
- **Fins de ligne** : un prompt envoyé à Claude garde ses `\n` tels quels (pas de conversion en `\r\n`). `.gitattributes` impose LF dans le dépôt.
- **Chemins** : les chemins sont résolus (noms courts 8.3 → noms longs) avant comparaison.
- **Processus de test** : au timeout, tout l'arbre est arrêté avec `taskkill /F /T` ; les commandes `.cmd` (npm, pnpm, yarn) sont lancées via `cmd /d /c`.
- **Couleurs** : activées automatiquement dans Windows Terminal et dans la console qui accepte les séquences ANSI ; `--no-color` ou `NO_COLOR` les coupent.

## Variables d'environnement et `.env`

```powershell
# Fichier .env dans le dossier d'où vous lancez workflow (ou dans %USERPROFILE%\.config\workflow-claude\.env)
Copy-Item .env.example .env
notepad .env                          # renseignez TYPESAFE_API_KEY

# Ou pour la session PowerShell courante seulement
$env:TYPESAFE_API_KEY = "votre_clé"
$env:CLAUDE_BIN = "C:\chemin\vers\claude.cmd"    # si le CLI n'est pas dans le PATH
```

Une variable déjà définie dans l'environnement n'est jamais écrasée par un fichier `.env`.

## Problèmes fréquents

| Symptôme | Cause | Solution |
| :--- | :--- | :--- |
| `Binaire Claude introuvable` | PATH pas rechargé après `npm install -g` | Rouvrir le terminal, ou définir `CLAUDE_BIN` |
| `python3` / `pytest` jamais trouvé dans le projet cible | Aucun interpréteur Python réel dans le PATH, ou projet sans venv | Créez un `.venv` dans le projet (utilisé en priorité) ou indiquez `--test-cmd` |
| Tests du projet qui bloquent | Mode « watch » (jest/vitest) | Géré : stdin fermé et `CI=true` ; sinon `--test-cmd "npm test -- --run"` |
| `Impossible de mettre en réserve vos modifications locales` | Sous-module modifié | Commitez ou annulez la modification du sous-module |

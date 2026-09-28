# 🛠️ Guide de Dépannage, FAQ & Diagnostics

Ce guide répertorie les situations d'erreur fréquentes, leurs causes exactes et les étapes concrètes de résolution pour **`Workflow-Claude-Code`**.

---

## 🔍 Diagnostics & Solutions Rapides

### 1. Binaire `claude` introuvable

#### Symptôme
```text
ClaudeCliError: Binaire Claude introuvable au chemin : 'claude'.
Assurez-vous que Claude Code est installé et accessible dans le PATH
ou définissez la variable d'environnement CLAUDE_BIN.
```

#### Diagnostic
L'orchestrateur recherche le binaire selon l'ordre suivant :
1. Variable d'environnement `CLAUDE_BIN`
2. `PATH` système via `shutil.which("claude")`
3. Dossier de versions Claude : `~/.config/Claude/claude-code/<version>/claude`
4. Chemins standards npm :
   - Linux/macOS : `~/.local/bin/claude`, `/usr/local/bin/claude`, `~/.npm-global/bin/claude`
   - Windows : `%APPDATA%\npm\claude.cmd`, `%LOCALAPPDATA%\Programs\Claude\claude.exe`, `C:\Program Files\nodejs\claude.cmd`

#### Solution
1. Vérifiez l'installation de Claude Code CLI :
   ```bash
   npm install -g @anthropic-ai/claude-code
   ```
2. Localisez le chemin du binaire :
   - **Linux/macOS :** `which claude`
   - **Windows (CMD/PowerShell) :** `where claude`
3. Si le chemin n'est pas dans votre `PATH`, configurez-le dans votre fichier `.env` :
   ```ini
   CLAUDE_BIN=/chemin/absolu/vers/claude
   ```

---

### 2. Session Claude Code non authentifiée

#### Symptôme
```text
ClaudeCliError: Session Claude Code non authentifiée. Erreur : Not logged in.
Exécutez `claude auth login` dans votre terminal pour activer votre session Claude Max 5x.
```

#### Solution
Le CLI Claude Code requiert une session interactive active pour utiliser votre abonnement sans clé API payante :
```bash
claude auth login
```
Suivez les instructions dans votre navigateur web. Une fois connecté, relancez `workflow`.

---

### 3. Conflit lors de la restauration du Stash (`stash_pop`)

#### Symptôme
```text
Stash Guard : Conflit de fusion détecté lors de la restauration du stash.
Vos modifications locales sont conservées dans le stash Git.
```

#### Cause
Vous avez modifié manuellement des fichiers sur votre copie de travail pendant que l'orchestrateur appliquait et validait des modifications sur la même portion de code.

#### Solution
**Vos données ne sont jamais perdues.** Stash Guard a conservé votre mise en réserve intacte :
1. Affichez la liste des stashs :
   ```bash
   git stash list
   ```
2. Inspectez le contenu de vos modifications :
   ```bash
   git stash show -p stash@{0}
   ```
3. Résolvez le conflit manuellement en appliquant le stash :
   ```bash
   git stash apply stash@{0}
   ```
   *(Puis supprimez le stash une fois résolu : `git stash drop stash@{0}`).*

---

### 4. Circuit Breaker Déclenché (`MAX_RETRIES` atteint)

#### Symptôme
```text
Circuit breaker : Nombre maximum d'itérations (4) atteint.
Arrêt forcé à l'étape 'CHECK_QUALITE' pour prévenir une consommation incontrôlée.
```

#### Cause
Le code produit a été rejeté consécutivement par l'audit Qualité, l'audit Sécurité ou par les tests unitaires du projet à chaque itération.

#### Solution
1. **Protection du Dépôt :** L'orchestrateur a automatiquement effectué un rollback propre pour ne laisser aucun fichier corrompu sur votre branche.
2. **Inspection de l'Audit :** Consultez le fichier `./output/WORKFLOW_AUDIT.md` pour lire les motifs précis des rejets formulés par Jev et Claude.
3. **Affiner le Prompt :** Précisez votre prompt initial pour réduire les ambiguïtés architecturales ou clarifier les contraintes.
4. **Augmenter les Tentatives :** Pour les refactorings très lourds, vous pouvez augmenter la limite :
   ```bash
   workflow --max-retries 6 "..."
   ```

---

### 5. Suite de Tests non Détectée

#### Symptôme
```text
Aucune suite de tests automatisée détectée dans le projet.
```

#### Diagnostic
L'orchestrateur recherche automatiquement :
- **Node.js :** `package.json` contenant un script `"test"` (autre que le placeholder npm par défaut).
- **Python :** Répertoire `tests/` ou `test/` contenant des fichiers `.py`, `pytest.ini` ou `setup.cfg`.
- **Rust :** `Cargo.toml` avec `cargo test`.
- **Go :** `go.mod` avec `go test ./...`.

#### Solution
Si vos tests utilisent une commande non standard ou un chemin spécifique, vous pouvez désactiver la vérification automatique via `--no-tests`, ou créer un script standard dans votre `package.json` ou configuration de test.

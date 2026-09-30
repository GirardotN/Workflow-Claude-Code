# Guide de Dépannage, FAQ & Diagnostics

Ce guide répertorie les situations d'erreur fréquentes, leurs causes exactes et les étapes concrètes de résolution pour **`Workflow-Claude-Code`**.

---

## Diagnostics & Solutions Rapides

> **Première étape pour tout problème d'environnement : `workflow --doctor`.** Il vérifie Git (et l'identité), le CLI Claude (version, options requises), la session (`claude auth status`), la présence de clés API payantes et de la clé TypeSafe, sans consommer de quota.

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

### 2 bis. Limite d'usage de l'abonnement atteinte

#### Symptôme
```text
ClaudeQuotaError: Limite d'usage de l'abonnement Claude atteinte : You've hit your limit · resets 5pm ...
```

#### Ce qui s'est passé
Le CLI a signalé `is_error` avec un message de quota. Le workflow s'arrête **immédiatement** (pas de reprise inutile) ; le rollback, le retour sur votre branche et la restauration de votre stash sont faits automatiquement.

#### Solution
Relancez après l'heure de réinitialisation indiquée dans le message. Pour limiter la consommation : tâches plus petites, `--max-retries` plus bas.

---

### 2 ter. Timeout de l'agent

Symptôme : `ClaudeTimeoutError: Timeout (900s) dépassé ...`. Augmentez `CLAUDE_TIMEOUT_DEV_SECONDS` (développement), `CLAUDE_TIMEOUT_SPEC_SECONDS` (exploration) ou `CLAUDE_TIMEOUT_SECONDS` (revues). Les timeouts ne sont pas repris automatiquement.

---

### 2 quater. Erreurs TypeSafe Jev (code de sortie 4)

| Message | Cause | Solution |
| :--- | :--- | :--- |
| `Clé TypeSafe absente …` | `TYPESAFE_API_KEY` absente de `.env` / de l'environnement | Renseignez-la, ou lancez avec `--mock` (simulation, validations non fiables) |
| `Clé TypeSafe refusée (HTTP 401/403)` | Clé invalide ou révoquée | Vérifiez la clé sur typesafe.ai ; `workflow --doctor` la teste |
| `TypeSafe Jev indisponible (HTTP 5xx)` / `Erreur de communication …` | Service ou réseau en panne après les reprises | Réessayez plus tard ; le dépôt a été remis en état |
| `max_tokens_exceeded` | Contexte trop long pour l'API | Baissez `JEV_MAX_STATE_CHARS` ou utilisez `--jev-send review-only` |
| `Réponse de TypeSafe Jev inattendue` | Format de réponse modifié côté service | Signalez-le : le workflow s'arrête volontairement plutôt que de valider |

Le workflow ne valide **jamais** du code faute de réponse de Jev : il s'arrête.

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
Arrêt forcé avant un nouveau cycle de développement pour prévenir une consommation incontrôlée.
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

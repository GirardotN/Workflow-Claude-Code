# Sous le Capot : Ingénierie & Sécurité Enterprise-Grade

Ce document détaille les solutions techniques durcies mises en œuvre dans **`Workflow-Claude-Code`** pour garantir la sécurité du système hôte et l'intégrité absolue des données du développeur.

---

## 1. Stash Guard : Sécurité Anti-Perte de Données Inconditionnelle

### Le Problème
Lorsqu'un agent de développement autonome applique des modifications défaillantes qui sont ensuite rejetées par la revue de qualité, la FSM doit annuler les changements via `git restore .` et `git clean -fd`.  
Si le développeur avait des modifications en cours non enregistrées sur son arbre de travail (*dirty working tree*), cette opération détruirait irréversiblement son travail personnel.

### L'Implémentation Stash Guard
1. **Détection Initiale :**  
   Avant toute action, `is_working_tree_clean()` vérifie l'état de l'arbre.
2. **Mise en Réserve Sécurisée :**  
   Si des modifications sont présentes (y compris des fichiers non suivis `untracked`), l'orchestrateur exécute :
   ```bash
   git stash push -u -m "workflow-auto-stash-<timestamp>"
   ```
3. **Garantie par gestionnaire de contexte (`IsolatedRun`, `isolation.py`) :**  
   L'ensemble du cycle de vie de la FSM s'exécute dans `with IsolatedRun(...)`, dont `__exit__` joue le rôle d'un bloc `finally:`. Même en cas d'exception non gérée, de crash réseau, d'erreur de sous-processus ou d'interruption manuelle par l'utilisateur (`SIGINT` / `Ctrl+C`), le bloc `finally` s'exécute inconditionnellement :
   ```python
   finally:
       if stashed and self.git:
           logger.info("Stash Guard : Restauration des modifications locales mises en réserve...")
           self.git.stash_pop()
   ```
4. **Gestion Non Destructive des Conflits :**  
   Si un conflit survient lors du `stash_pop()` (modification concurrente d'un fichier), l'orchestrateur n'écrase rien, avertit l'utilisateur et conserve le stash intact dans l'historique Git (`git stash list`).

---

## 2. Oracle Baseline (Cycle 0) & Normalisation Anti-Jitter

### Le Problème
1. **Échecs Préexistants :** Dans les bases de code réelles, certains tests de la suite peuvent déjà échouer avant l'intervention de l'IA. Si l'orchestrateur considérait aveuglément tout échec de test comme une régression, l'agent entrerait dans une boucle infinie de correction sur du code non sollicité.
2. **Jitter Temporel :** Les frameworks modernes (`pytest`, `jest`, `cargo test`) affichent des métriques de temps variables d'une exécution à l'autre (ex: `passed in 0.42s` vs `passed in 0.45s`). Une comparaison stricte de chaînes de caractères échouerait faussement.

### L'Implémentation
1. **Diagnostic Initial (Cycle 0) :**  
   Avant de modifier un seul fichier, l'orchestrateur exécute la commande de test détectée (`TestRunner.run_tests()`) et snapshotte le résultat (`baseline_tests_passed` et `baseline_output`).
2. **Normalisation Déterministe :**  
   La fonction `normalize_test_output()` expurge toutes les variations temporelles et les pourcentages de progression :
   ```python
   def normalize_test_output(output: str) -> str:
       # Supprime les indications de millisecondes et secondes (ex: '45ms', '0.42s')
       cleaned = re.sub(r"\b\d+(\.\d+)?\s*(s|ms|seconds?)\b", "", output, flags=re.IGNORECASE)
       cleaned = re.sub(r"\(duration:\s*\d+(\.\d+)?s\)", "", cleaned, flags=re.IGNORECASE)
       cleaned = re.sub(r"(\[|\()\s*\d+%\s*(\]|\))", "", cleaned)
       return re.sub(r"\s+", " ", cleaned).strip()
   ```
3. **Différenciation de Régression :**  
   Si la suite de tests échoue après édition mais que la sortie normalisée correspond exactement à la baseline initiale, l'orchestrateur en déduit qu'il s'agit d'un problème préexistant et n'interrompt pas le flux.

---

## 3. Appels à Claude : prompt sur stdin, environnement nettoyé, outils par rôle

### Le prompt ne passe jamais par la ligne de commande
Le texte envoyé à `claude -p` (spécification, diff, revue…) est transmis sur **l'entrée standard**, en octets exacts (UTF-8, sans conversion de fins de ligne). Conséquences :
- aucune limite de longueur de ligne de commande (Windows : ~8 ko via `cmd /c`, ~32 ko sinon) ;
- aucun contenu utilisateur n'est jamais interprété par un shell (`& | % ^ "`, `$(...)`…) : un test envoie un prompt de 240 000 caractères bourré de métacaractères et vérifie qu'il arrive intact.

Sous Windows, le shim npm `claude.cmd` ne fait que lancer `node_modules/@anthropic-ai/claude-code/bin/claude.exe` : l'orchestrateur appelle **directement ce binaire natif** (pas de `cmd.exe`). Si le binaire natif est introuvable, il retombe sur `cmd /d /c claude.cmd`, en refusant tout argument contenant un caractère que `cmd` interpréterait. `shell=True` n'est jamais utilisé.

### « Zéro crédit API » : environnement du sous-processus
Par défaut, `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX` et `CLAUDE_CODE_USE_FOUNDRY` sont **retirées de l'environnement du processus Claude** : le CLI n'a d'autre choix que la session de l'abonnement (`claude auth login`). Le CLI affiche un message si l'une d'elles est présente dans votre environnement. `--allow-api-key` (ou `ALLOW_API_KEY=1`) lève cette protection, à vos risques (facturation à l'usage, avertissement affiché). `workflow --doctor` vérifie tout cela sans consommer de quota.

### Isolation cognitive : ce que chaque agent peut voir et faire
| Rôle | Outils Claude | Répertoire de travail |
| :--- | :--- | :--- |
| Spécification (mode In-Repo) | `Read, Grep, Glob` (lecture seule) | le projet |
| Développement | `Read, Edit, Write, Grep, Glob` (+ `Bash` restreint avec `--allow-bash`) | le projet |
| Qualité, Sécurité, Feedback, Doc & Commit | **aucun** (`--tools=`) | **répertoire temporaire vide** |
| Tous les rôles en mode Standalone | **aucun** | répertoire temporaire vide |

Les relecteurs ne peuvent donc pas lire votre dépôt : ils ne voient que le texte qu'on leur envoie (le diff, la revue qualité pour l'agent sécurité). Ce n'est plus une simple consigne de prompt.

### Politique `--allow-bash` (pas un bac à sable)
Avec `--allow-bash`, l'agent de développement peut lancer uniquement une liste blanche de commandes (`pytest`, `python -m unittest`, `npm test`, `cargo test`, `go test`, `dotnet test`, `git status/diff/log/show`, `ls`, `cat`). Sont **interdits** : toutes les commandes git qui déplacent HEAD ou l'index (`push`, `commit`, `checkout`, `reset`, `clean`, `stash`, `branch`, `merge`, `rebase`, `restore`, `add`), `rm`, `sudo`, `curl`, `wget`, `npm publish/install`, `pip install` — l'état Git appartient à l'orchestrateur. Attention : `pytest` exécute le code de votre projet ; ce n'est pas une isolation système.

### Erreurs, quotas et délais
- Le CLI renvoie un JSON avec `is_error: true` (même avec le code de sortie 0 ou 1) : il est lu **avant** le code de sortie. Une erreur n'est plus jamais traitée comme un résultat valide.
- Erreurs typées : `ClaudeAuthError` (session), `ClaudeQuotaError` (limite d'usage de l'abonnement : arrêt propre, aucune reprise), `ClaudeTimeoutError`, `ClaudeCliError` (autres). Les erreurs transitoires (serveur surchargé, 502/503/529) sont reprises `CLAUDE_MAX_RETRIES` fois (défaut 2).
- Délais par rôle : développement 900 s, spécification 300 s, autres 180 s (`CLAUDE_TIMEOUT_DEV_SECONDS`, `CLAUDE_TIMEOUT_SPEC_SECONDS`, `CLAUDE_TIMEOUT_SECONDS`).
- Le coût, la durée, le nombre de tours et les refus de permission renvoyés par le CLI sont exposés dans `ClaudeCliClient.last_result`.

### Autres garanties
- **Git :** `LC_ALL=C` et `GIT_TERMINAL_PROMPT=0` sur toutes les commandes ; sorties prévisibles quelle que soit la langue de l'OS.
- **Encodage :** `encoding="utf-8"` explicite ; `sys.stdout` reconfiguré sous Windows et affichage tolérant aux erreurs d'encodage.

---

## 4. Parseur Lexical d'Accolades Équilibrées

### Le Problème
Lorsque Claude retourne un format JSON structuré (`--output-format json`), le champ de résultat contient fréquemment du code source avec des accolades imbriquées (TypeScript, JSON, C#).  
Les expressions régulières non-gloutonnes classiques (`re.search(r"\{.*?\}")`) s'arrêtent à la toute première accolade fermante interne, corrompant irrémédiablement le payload JSON.

### L'Automate d'États Lexical
`src/workflow_claude/clients/claude_cli.py` implémente un analyseur lexical à profondeur variable qui gère l'état des chaînes de caractères et les caractères d'échappement :

```python
start_idx = raw_stdout.find("{")
while start_idx != -1:
    depth = 0
    in_string = False
    escape = False
    for i in range(start_idx, len(raw_stdout)):
        char = raw_stdout[i]
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if not in_string:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = raw_stdout[start_idx : i + 1]
                    try:
                        data = json.loads(candidate)
                        if isinstance(data, dict):
                            for key in ("result", "text", "content"):
                                if key in data:
                                    return str(data[key]).strip()
                    except json.JSONDecodeError:
                        pass
                    break
    start_idx = raw_stdout.find("{", start_idx + 1)
```

Ce parseur garantit une extraction 100 % fiable de la réponse utile, même en présence de logs de préambule ou de code imbriqué complexe.

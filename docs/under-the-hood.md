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

## 2. Oracle de tests : baseline par ensembles de tests en échec

### Le problème
1. **Échecs préexistants** : si l'orchestrateur considérait tout échec comme une régression, l'agent bouclerait sur du code non sollicité.
2. **Bruit** : temps d'exécution, adresses mémoire, chemins temporaires, ordre des tests changent d'une exécution à l'autre. Comparer la sortie texte brute produit des fausses régressions (ou masque des vraies).

### L'implémentation
1. **Baseline (cycle 0)** : avant de toucher au code, la suite est exécutée ; les **identifiants des tests en échec** sont extraits de la sortie (pytest, unittest, jest, vitest, go, cargo, dotnet).
2. **Régression = un test échoue qui n'échouait pas dans la baseline** (`is_regression`). Mêmes échecs avec un texte différent : pas une régression. Moins d'échecs : pas une régression. Si les identifiants sont illisibles (crash, erreur de compilation), repli sur la comparaison de la sortie normalisée (temps, pourcentages et espaces neutralisés).
3. **Nettoyage** : les fichiers non suivis créés par les tests (caches, rapports) sont supprimés avant le diff et le commit.
4. **Dernier cycle** : si le circuit breaker s'arrête alors que les tests échouent encore, le message d'erreur l'indique avec les premiers tests concernés (`report.tests_failed`).

### Exécution robuste
- **Commande** (par priorité) : `--test-cmd` / `TEST_COMMAND` → `.workflow.toml` du projet (`[tests]` `command`, `timeout`) → détection automatique : Node (npm / pnpm / yarn / bun d'après le lockfile), Python (**venv du projet** s'il existe, `pytest` si importable, sinon `unittest`), .NET, Maven, Gradle (wrappers du projet), Rust, Go. Le raccourci `python3` du Microsoft Store n'est jamais utilisé.
- **Sans terminal** : stdin fermé (un mode « watch » ne peut pas bloquer), `CI=true`, `NO_COLOR=1`, `PYTHONDONTWRITEBYTECODE=1`, codes ANSI retirés de la sortie.
- **Timeout** (`--test-timeout`, `TEST_TIMEOUT_SECONDS`, 300 s par défaut ou `timeout` de `.workflow.toml`) : tout l'**arbre de processus** est tué (`taskkill /T` sous Windows, groupe de processus sous POSIX), pas seulement le parent.
- **Sortie** plafonnée à `MAX_TEST_OUTPUT_CHARS` (début et fin conservés) ; la liste des tests en échec est extraite avant la troncature.

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

Ce parseur extrait la réponse utile même en présence de logs de préambule ou de code imbriqué (cas couverts par des tests).

---

## 5. TypeSafe Jev : format réel, fail-closed et confidentialité

### Format de l'API (relevé contre le service)
`POST /v1/systemone` avec `{"model": "jev-latest", "state": "<texte>", "questions": {...}}`.
- **Routage** : question `choice` (`instructions` + `criteria` = options et descriptions) → `answers.<clé>.choice`, `confidence`, `probabilities`. Les descriptions des niveaux de complexité et des spécialités sont transmises à Jev.
- **Validation** : question `noul` → `answers.<clé>.noul` ∈ [0, 1] = probabilité que la réponse soit « oui ». Mesures : diff sain 0,77–0,89 ; diff avec injection SQL et division par zéro 0,01.
- Erreurs : `401/403` authentification ; `400` requête invalide ou `max_tokens_exceeded` (état > ~32 000 tokens) ; `422` schéma. `/v1/decide` n'existe pas (404).

> Jusqu'à la version précédente, le client lisait une clé `results` qui n'existe pas dans la réponse : la probabilité par défaut (1.0) faisait **valider toutes les revues**. Un test de non-régression couvre ce cas.

### Fail-closed
Une réponse absente, hors format (`noul` manquant, hors [0,1], option inconnue, JSON invalide, clé `answers` absente) lève `JevApiError` : **jamais** de validation par défaut. Sur erreur réseau, 429 ou 5xx, 2 reprises avec attente ; ensuite le workflow s'arrête, le dépôt est remis en état et le code de sortie est 4. Sans clé TypeSafe, le programme refuse de démarrer ; le mode simulation n'existe que via `--mock` (bandeau permanent, `report.jev_mode = "mock"`).

### Ce qui est envoyé à Jev, et comment
| Étape | Contenu | Traitement |
| :--- | :--- | :--- |
| Routage (complexité, spécialité) | la spécification produite par Claude | secrets masqués, plafonné |
| Validation qualité / sécurité (`--jev-send full`) | diff + revue | **secrets masqués**, diff tronqué au milieu si trop long (la revue est conservée) |
| Validation (`--jev-send review-only`) | revue seule | aucun code transmis |

Le masquage (`jev_context.py`) remplace par `[REDACTED:<type>]` : clés Anthropic/OpenAI/GitHub/AWS/Google/Slack, JWT, blocs de clé privée, `Bearer …`, identifiants dans les URL et valeurs de `password/secret/token/api_key/...`. C'est un masquage « au mieux » par expressions régulières, pas une garantie : pour ne rien transmettre du code, utilisez `--jev-send review-only`. La clé TypeSafe n'apparaît jamais dans les erreurs, les journaux ni les contextes.

### Contrôle de cohérence
Les relecteurs terminent par `VERDICT: PASS` ou `VERDICT: FAIL`. Si ce verdict contredit la décision de Jev, un avertissement est journalisé et la contradiction est enregistrée dans `report.decisions` (`consistent: false`) ; la décision de Jev s'applique.

### Traçabilité
Chaque décision Jev (étape, résultat, probabilité, seuil, latence, tokens, tentatives, troncature) est enregistrée dans `report.decisions` et listée dans le résumé du CLI.

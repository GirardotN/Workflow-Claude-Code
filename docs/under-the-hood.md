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
3. **Garantie par Bloc `finally:` :**  
   L'ensemble du cycle de vie de la FSM est encapsulé dans un bloc `try: ... finally:`. Même en cas d'exception non gérée, de crash réseau, d'erreur de sous-processus ou d'interruption manuelle par l'utilisateur (`SIGINT` / `Ctrl+C`), le bloc `finally` s'exécute inconditionnellement :
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

## 3. Sécurité des Sous-Processus & Résilience Windows

### Le Problème
Sous Windows, le binaire Claude Code est généralement installé globalement par npm sous la forme d'un script batch `claude.cmd`. L'appel direct via `subprocess.run(["claude.cmd", ...])` échoue sous Windows sans shell. Cependant, activer `shell=True` introduit une vulnérabilité critique d'injection shell si le prompt utilisateur contient des métacaractères (`&`, `|`, `%`, `^`).

### L'Implémentation Sécurisée
1. **Bannissement de `shell=True` :**  
   Le paramètre `shell=False` est rigoureusement maintenu sur toutes les plateformes.
2. **Invocation Sécurisée via `comspec` :**  
   Sous Windows, la commande est enveloppée de manière contrôlée :
   ```python
   if os.name == "nt" and self.binary_path.lower().endswith((".cmd", ".bat")):
       comspec = os.environ.get("COMSPEC", "cmd.exe")
       exec_cmd = [comspec, "/d", "/c", self.binary_path] + cmd[1:]
   ```
   Le commutateur `/d` désactive l'exécution des commandes d'AutoRun inscrites dans le Registre Windows.
3. **Normalisation Linguistique Git (`LC_ALL=C`) :**  
   Toutes les commandes Git injectent la variable d'environnement `LC_ALL=C` pour garantir des sorties prévisibles et homogènes (évite les divergences linguistiques des messages d'erreur selon la locale de l'OS).
4. **Encodage UTF-8 Universel :**  
   Gestion explicite de `encoding="utf-8"` et `errors="replace"` avec reconfiguration de `sys.stdout` pour Windows Terminal / PowerShell, éliminant les erreurs `UnicodeDecodeError` fréquentes avec la page de code CP1252.

---

## 4. Parseur Lexical d'Accolades Équilibrées

### Le Problème
Lorsque Claude retourne un format JSON structuré (`--output-format json`), le champ de résultat contient fréquemment du code source avec des accolades imbriquées (TypeScript, JSON, C#).  
Les expressions régulières non-gloutonnes classiques (`re.search(r"\{.*?\}")`) s'arrêtent à la toute première accolade fermante interne, corrompant irrémédiablement le payload JSON.

### L'Automate d'États Lexical
`clients/claude_cli.py` implémente un analyseur lexical à profondeur variable qui gère l'état des chaînes de caractères et les caractères d'échappement :

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

# Guide d'Utilisation Avancé (Cookbooks)

Scénarios concrets d'utilisation de **`Workflow-Claude-Code`**. Avant tout : `workflow --doctor`, et un premier essai sur un **dépôt jetable** (voir [Limites connues](../README.md#limites-connues)).

---

## Cookbook 1 : Modifier un composant dans un projet React / TypeScript

### Contexte
Application front-end volumineuse (centaines de composants, `node_modules` de plusieurs gigaoctets). Vous voulez trier un tableau par date décroissante et ajouter un sous-composant `Badge.tsx`.

### Commande
```bash
workflow --project-dir /home/dev/frontend-app \
         "Modifie le composant TabX.tsx pour trier les transactions par date décroissante et crée un sous-composant Badge.tsx pour afficher le statut"
```

### Ce qui se passe
1. **Mise à l'abri de votre travail** : vos modifications non commitées sont mises en réserve ; le travail se fait sur la branche `workflow/ai-<timestamp>`.
2. **Exploration** : l'agent de spécification (lecture seule) reçoit la consigne d'ignorer `node_modules`, `dist`, `build`, `.next`, `.git`, `coverage`… (c'est une consigne de prompt, complétée côté revue par un filtre de diff).
3. **Nouveaux fichiers visibles** : `git add -N .` (intent-to-add) fait apparaître `Badge.tsx` dans le diff sans le stager. Les lockfiles et fichiers générés (`package-lock.json`, `dist/`, `*.min.js`…) sont **retirés du diff relu** par les agents (la liste des fichiers exclus leur est indiquée).
4. **Tests** : `npm test` (ou pnpm/yarn/bun selon le lockfile), comparés à la baseline initiale.
5. **Revues** : l'agent qualité ne voit que le diff (aucun outil, répertoire vide) ; Jev valide ou rejette sur sa probabilité `noul`.
6. **Documentation** : l'agent doc met à jour README/CHANGELOG/`docs/` dans le même commit ; tout ce qui n'est pas de la doc est annulé.
7. **Fin** : commit sur `workflow/ai-*`, retour sur votre branche, proposition de fusion ; puis restauration de votre travail en cours.

---

## Cookbook 2 : Corriger un bug avec le filet de tests existant

### Contexte
Une suite `pytest` échoue sur une route d'authentification après une modification de schéma.

### Commande
```bash
workflow --project-dir /home/dev/auth-service \
         --test-cmd "pytest -x -q tests/unit" \
         "Corrige la validation JWT dans auth/security.py pour accepter les tokens sans champ 'sub' sans lever d'erreur 500"
```

### Déroulé de la boucle d'auto-réparation
1. **Baseline (cycle 0)** : la suite est exécutée avant toute modification ; les **tests déjà en échec** sont identifiés (par exemple `tests/legacy_test.py::test_old_bug`).
2. **Édition** : l'agent modifie `auth/security.py`.
3. **Régression ?** Un test échoue qui n'échouait pas dans la baseline : la trace d'erreur est renvoyée à l'agent de développement avec la consigne de rendre la suite verte. Les échecs préexistants (même avec un temps d'exécution ou un chemin temporaire différent) ne comptent pas.
4. **Validation** : tests sans nouvelle défaillance, puis revue qualité et décision de Jev.
5. **Circuit breaker** : au-delà de `--max-retries` (4), arrêt propre ; le message indique les tests encore en échec et `report.json` / `WORKFLOW_AUDIT.md` détaillent chaque cycle.

Variante : fixez la commande une fois pour toutes dans `.workflow.toml` à la racine du projet :
```toml
[tests]
command = "pytest -x -q tests/unit"
timeout = 600
```

---

## Cookbook 3 : Utilisation en script ou en CI

### Principe
Pas d'invite interactive à gérer : utilisez les codes de sortie et la sortie JSON.

```bash
workflow --project-dir . --merge --json --no-color \
         "Migre les imports Pydantic v1 (BaseModel) vers Pydantic v2" > report.json
status=$?

case $status in
  0)   echo "Succès : $(python -c 'import json;print(json.load(open("report.json"))["commit_hash"])')" ;;
  2)   echo "Workflow incomplet : voir output/WORKFLOW_AUDIT.md" ;;
  3)   echo "Claude : session ou quota (voir report.json)" ;;
  4)   echo "TypeSafe Jev indisponible ou clé refusée" ;;
  *)   echo "Erreur ($status)" ;;
esac
```

- `--merge` fusionne automatiquement `workflow/ai-*` dans la branche d'origine ; sans lui, la fusion est refusée en non-interactif (la branche est conservée pour une revue humaine).
- `--commit` n'est pas nécessaire : le succès est toujours committé sur la branche d'isolation.
- `--json` : le rapport (sans contenu de prompts) est sur **stdout**, l'affichage humain sur stderr.
- Un conflit de fusion n'échoue pas le run : `merged` vaut `false` et la branche est conservée.

### Authentification de Claude en CI
`workflow` utilise la **session de votre abonnement** (`claude auth login`), qui est interactive : sur un serveur de CI, il faut une machine où cette session existe déjà (poste de travail, runner auto-hébergé). Pour utiliser une clé d'API à la place — **avec facturation à l'usage** — fournissez-la dans l'environnement et passez `--allow-api-key` ; sans cette option, ces variables sont volontairement retirées du processus Claude.

### Exemple de job GitHub Actions (runner auto-hébergé déjà authentifié)
```yaml
name: Automated Migration
on:
  workflow_dispatch:

jobs:
  migrate:
    runs-on: self-hosted        # machine où `claude auth login` a été fait
    steps:
      - uses: actions/checkout@v4
      - run: pipx install --force .
      - name: Run
        env:
          TYPESAFE_API_KEY: ${{ secrets.TYPESAFE_API_KEY }}
        run: workflow --project-dir . --merge --json --no-color "Met à jour la configuration de logging structuré" > report.json
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: workflow-report
          path: |
            report.json
            output/
```

---

## Cookbook 4 : Revoir ce que l'agent a fait, sans rien fusionner

```bash
workflow --project-dir . "Ajoute une validation d'e-mail dans signup.py"   # répondre « n » à la fusion
git log --oneline main..workflow/ai-1759312345      # commits proposés
git diff main...workflow/ai-1759312345              # diff complet
git merge workflow/ai-1759312345                    # ou : git branch -D workflow/ai-1759312345
```
`output/WORKFLOW_AUDIT.md` résume le run : tests (baseline et dernier cycle), décisions de Jev avec leur probabilité, modèles, durées et coût équivalent API.

## Cookbook 5 : Ne rien envoyer du code à TypeSafe

```bash
workflow --jev-send review-only --project-dir . "Refactorise le module de paiement"
```
Jev ne reçoit alors que la **revue** rédigée par l'agent qualité, jamais le diff. Les validations peuvent être moins précises (Jev juge sans voir le code), mais aucun extrait de code ne quitte votre machine vers ce service.

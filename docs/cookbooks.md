# Guide d'Utilisation Avancé (Cookbooks)

Ce guide rassemble des scénarios d'ingénierie réels et reproductibles montrant comment tirer parti de **`Workflow-Claude-Code`** sur des bases de code complexes.

---

## Cookbook 1 : Refactoring In-Situ dans un Projet React / TypeScript

### Contexte
Dans une application front-end volumineuse comprenant des centaines de composants et un répertoire `node_modules` de plusieurs gigaoctets, vous souhaitez modifier la logique de tri d'un tableau tout en créant un nouveau composant d'affichage.

### Commande
```bash
workflow --project-dir /home/dev/frontend-app \
         --branch \
         "Modifie le composant TabX.tsx pour trier les transactions par date décroissante et crée un sous-composant Badge.tsx pour afficher le statut"
```

### Mécanique sous le capot
1. **Filtrage Intelligent des Répertoires Lourds :**  
   L'orchestrateur injecte automatiquement une consigne stricte interdisant l'indexation de `node_modules`, `dist`, `.next`, `.git`.
2. **Intent-to-Add (`git add -N .`) :**  
   Lorsque Claude crée le nouveau fichier `Badge.tsx`, Git ne l'inclut pas par défaut dans `git diff` tant qu'il n'est pas indexé. `Workflow-Claude-Code` applique automatiquement `git add -N .` (intent-to-add), rendant le nouveau fichier immédiatement visible dans le diff unifié sans forcer un staging complet.
3. **Revue Ciblée sur le Diff :**  
   L'agent de qualité inspecte uniquement le patch Git (l'écart exact avec HEAD) pour vérifier la conformité des types TypeScript et l'absence de régression de rendu.
4. **Validation et Fusion :**  
   Après accord de Jev, le commit est créé et l'orchestrateur propose la fusion dans votre branche de travail.

---

## Cookbook 2 : Correction de Bug Python / FastAPI avec Auto-Réparation TDD

### Contexte
Une suite de tests `pytest` échoue sur une route d'authentification après une modification de schéma Pydantic. Vous confiez la résolution du bogue à l'orchestrateur en vous appuyant sur la suite de tests existante comme oracle de vérité.

### Commande
```bash
workflow --project-dir /home/dev/auth-service \
         --run-tests \
         "Corrige la validation JWT dans auth/security.py pour accepter les tokens sans champ 'sub' optionnel sans lever d'erreur 500"
```

### Déroulé de la Boucle d'Auto-Guérison
1. **Baseline Cycle 0 :**  
   L'orchestrateur exécute préalablement la suite de tests pour vérifier l'état de santé initial. Si un test legacy sans rapport échouait déjà, son empreinte est enregistrée pour ne pas en tenir rigueur à l'agent.
2. **Édition In-Situ :**  
   Claude modifie chirurgicalement `auth/security.py`.
3. **Capture d'Erreur :**  
   Le runner de tests exécute `pytest`. Si une assertion échoue (code de retour différent de 0), l'orchestrateur capture la trace d'erreur standard :
   ```text
   FAILED tests/test_security.py::test_jwt_optional_sub - KeyError: 'sub'
   ```
4. **Rétroaction Corrective :**  
   La trace d'erreur est immédiatement transmise au modèle de développement avec pour consigne impérative de corriger le code pour rendre la suite de tests verte.
5. **Validation :**  
   Dès que l'oracle de tests valide l'exécution (code retour 0), la revue Qualité Jev prend le relais.

---

## Cookbook 3 : Intégration CI/CD ou Scripts Batch (Mode Zero Interaction)

### Contexte
Vous orchestrez une migration automatisée sur plusieurs dépôts Git au sein d'un pipeline d'intégration continue (ou d'un script bash de maintenance nocturne). Aucune invite interactive ne doit bloquer l'exécution.

### Commande
```bash
workflow --project-dir /srv/workspace/microservice \
         --commit \
         --merge \
         -y \
         --run-tests \
         "Migre l'ensemble des imports Pydantic v1 (BaseModel) vers les syntaxes Pydantic v2"
```

### Options Clés pour l'Automatisation
- `--commit` : Crée automatiquement le commit Git conventionnel dès que la solution est validée.
- `--merge` : Fusionne immédiatement la branche d'isolation `workflow/ai-*` dans la branche active.
- `-y` (`--yes`) : Répond oui à toutes les invites de confirmation interactives.
- `--run-tests` : Bloque le merge si la suite de tests n'est pas strictement verte.

### Exemple de Job GitHub Actions
```yaml
name: Automated Migration
on:
  workflow_dispatch:

jobs:
  migrate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - run: npm install -g @anthropic-ai/claude-code
      - run: pip install -e .
      - name: Run Autonomous Migration
        run: |
          workflow --project-dir . --commit --merge -y --run-tests "Met à jour la configuration de logging structuré"
```

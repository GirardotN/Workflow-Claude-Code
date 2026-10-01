# ADR 0003 — Isolation Git par branche + stash (et non par worktree)

- **Statut :** Accepté
- **Date :** 2026-10-01

## Contexte

L'agent de développement modifie des fichiers **dans votre dépôt**. Il faut garantir que (1) votre travail en cours n'est jamais perdu, (2) une tentative rejetée disparaît sans trace, (3) le résultat validé arrive proprement dans l'historique, (4) rien ne reste en plan en cas d'erreur, de timeout ou de Ctrl-C.

## Décision

`IsolatedRun` (`isolation.py`), gestionnaire de contexte :
1. **Préconditions** avant toute action : dépôt avec au moins un commit, identité Git présente (si on va committer).
2. **Mise en réserve** (`git stash push -u`, retrouvée ensuite **par son message**, jamais « le dernier stash »). Si elle échoue ou laisse un arbre sale (sous-module modifié), on **refuse** de démarrer.
3. **Branche de travail** `workflow/ai-<timestamp>[-n]` créée depuis le commit courant (HEAD détaché géré).
4. Sur **toute sortie** : rollback (`reset --hard HEAD` + `clean -fd`) si non validé, retour sur la branche d'origine, suppression de la branche si vide, **puis** restauration du stash — sur la branche d'origine, jamais sur la branche de travail.
5. Succès : commit **systématique** sur la branche de travail, puis fusion (`--merge`, ou sur décision de l'utilisateur) ; en cas de conflit, `merge --abort` et branche conservée.

## Pourquoi pas un `git worktree`

- Un worktree sépare les fichiers mais **pas les dépendances installées** (`node_modules`, `.venv`, caches de build) : les tests y échoueraient ou demanderaient une réinstallation longue.
- L'utilisateur perdrait l'accès à l'agent dans son éditeur et ses outils (chemin différent).
- La complexité (nettoyage de worktrees orphelins après un crash) dépasse le bénéfice pour l'usage visé.

## Conséquences

- (+) Votre branche n'est jamais modifiée sans votre accord ; testé sur de vrais dépôts (échec, exception, Ctrl-C, hook qui refuse, sous-module, branche qui avance en cours de route, conflit de fusion).
- (−) **Un run à la fois par dépôt** (branche, stash et index partagés).
- (−) Pendant le run, votre arbre de travail est celui de l'agent : ne le modifiez pas (vos changements sont en réserve).
- (−) Un conflit à la restauration du stash laisse vos modifications dans `git stash list` (jamais perdues) et le signale.

## Alternatives écartées

- **`git worktree`** : voir ci-dessus (pourrait devenir une option pour les projets sans dépendances installées).
- **Travailler directement sur la branche de l'utilisateur** (`--no-branch`) : reste possible mais sans la garantie « votre branche n'est pas touchée » ; le rollback protège alors seulement contre les tentatives rejetées.
- **Option `allow_dirty`** (agir sur un arbre sale) : supprimée, incompatible avec le rollback (détruisait le travail de l'utilisateur).

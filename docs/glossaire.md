# Glossaire

Les termes que vous croiserez dans l'outil et sa documentation.

| Terme | Signification |
| :--- | :--- |
| **Agent** | Un appel au CLI Claude Code avec un rôle précis (spécification, développement, revue qualité, revue sécurité, documentation) et des droits limités à ce rôle. |
| **Claude Code CLI** | L'outil officiel d'Anthropic en ligne de commande. `workflow` le pilote avec `claude -p` et utilise **votre abonnement** (aucune clé d'API facturée à l'usage). |
| **TypeSafe Jev** | Un service externe de décision. Il ne raisonne pas sur le code : on lui soumet un texte et une question, il répond par une option (routage) ou par une **probabilité** de « oui » (validation). C'est lui qui dit si le travail est valide. Il faut une clé API TypeSafe. |
| **`noul`** | Le nom du type de question de validation de Jev : un nombre entre 0 et 1, la probabilité que la réponse soit « oui ». Validé si ce nombre atteint le seuil (0,5 par défaut, `JEV_THRESHOLD`). |
| **Fail-closed** | Principe de sûreté : si Jev ne répond pas ou répond n'importe quoi, le workflow **s'arrête**. Il ne valide jamais « par défaut ». |
| **FSM / machine à états** | L'enchaînement fixe des étapes : spécification, routage, développement, tests, revue(s), documentation, commit. Le déroulé est décidé par le programme, pas par un modèle. |
| **Routage** | Jev classe la tâche (simple, moyenne, complexe) et la spécialité (Python, Node.js, UI, C#). Cela décide des modèles utilisés (Opus seulement pour le complexe) et si la revue sécurité a lieu (pas pour le simple). |
| **Mode In-Repo** | L'outil modifie un dépôt Git existant (`--project-dir`). |
| **Mode Standalone** | L'outil génère du code seul, sans projet (`--standalone`) : un fichier dans le dossier de rapports. |
| **Branche d'isolation** | `workflow/ai-<nombre>` : la branche où l'agent travaille. Votre branche n'est pas modifiée tant que vous ne fusionnez pas. |
| **Stash / Stash Guard** | Mise de côté (`git stash`) de vos modifications non commitées avant le run, restaurées à la fin. C'est ce qui protège votre travail en cours. |
| **Rollback** | Retour de l'arbre de travail à son état d'avant la tentative (quand le travail est rejeté ou en cas d'erreur). |
| **Oracle de tests / baseline** | Vos propres tests servent d'arbitre. La *baseline* est leur résultat **avant** toute modification : un test qui échouait déjà n'est pas imputé à l'agent. Une *régression* est un test qui échoue maintenant et n'échouait pas avant. |
| **Circuit breaker** | Limite du nombre de cycles de correction (`--max-retries`, 4 par défaut). Au-delà, arrêt propre : cela évite de consommer votre quota en boucle. |
| **Isolation cognitive** | Les agents de revue ne voient que le diff : aucun outil, répertoire de travail vide. Ils ne peuvent ni lire le reste de votre dépôt ni être influencés par le plan du développeur. |
| **`VERDICT: PASS/FAIL`** | Dernière ligne demandée aux relecteurs. Sert de contrôle : si elle contredit Jev, l'incohérence est journalisée (Jev décide). |
| **DOC_EDIT** | Étape où un agent met à jour la documentation existante de votre projet (README, CHANGELOG, `docs/`) dans le même commit. Tout ce qui n'est pas de la documentation est annulé automatiquement. |
| **`--mock`** | Mode simulation : Claude et Jev sont simulés, rien n'est modifié dans votre projet (sauf un fichier marqueur), les validations ne sont **pas fiables**. |
| **Quota** | La limite d'usage de votre abonnement Claude. Quand elle est atteinte, le workflow s'arrête proprement (code de sortie 3). |
| **Code de sortie** | Le nombre renvoyé par la commande : 0 succès, 1 erreur, 2 workflow incomplet, 3 Claude, 4 Jev, 130 Ctrl-C. Utile dans des scripts et en CI. |

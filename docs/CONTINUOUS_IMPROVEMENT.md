# EtR — Boucle d'amélioration continue

Objectif : empêcher qu'une erreur déjà rencontrée soit répétée sans détection.

## Règle de validation

Une modification n'est jamais considérée comme terminée uniquement parce qu'un script, un workflow ou un service retourne `success`.

Pour toute modification qui touche le Raspberry, la validation finale doit porter sur le résultat réel observé sur la machine : service actif, processus attendu, fichier installé au bon emplacement, comportement fonctionnel et, pour une modification graphique, preuve visuelle sur le bureau.

## Boucle obligatoire après chaque incident

1. **Observation** — décrire exactement le symptôme constaté par l'utilisateur ou par la machine.
2. **Cause racine** — identifier pourquoi le système a pu sembler correct alors qu'il ne l'était pas.
3. **Correctif** — corriger la cause, pas uniquement le symptôme.
4. **Preuve réelle** — vérifier le résultat sur le Raspberry lui-même.
5. **Test de non-régression** — ajouter un contrôle qui échouera automatiquement si le même problème réapparaît.
6. **Capitalisation** — inscrire l'incident et la règle nouvelle dans le tableau ci-dessous.

## Registre des erreurs et apprentissages

| Date | Incident | Cause racine | Nouvelle règle de non-régression |
|---|---|---|---|
| 2026-09-06 | La Fabrique semblait déployée mais l'ancien comportement restait visible | Le workflow vérifiait surtout GitHub et la passerelle, pas la chaîne réellement exécutée depuis l'icône du bureau | Toujours vérifier `desktop -> launcher installé -> systemd -> processus réel` sur le Raspberry |
| 2026-09-06 | Le raccourci du bureau ne permettait pas d'identifier clairement la version installée | Aucun numéro de version visible côté opérateur | Le raccourci de la Fabrique porte une version explicite, ex. `SD V1.1`, et le déploiement vérifie le nom installé |
| 2026-09-06 | La barre de progression était codée mais invisible | Le processus Tkinter était lancé en arrière-plan sans preuve qu'une fenêtre était réellement affichée | Toute modification graphique doit être validée par présence du processus graphique et preuve visuelle sur le bureau |
| 2026-09-06 | Un déploiement est resté `pending` | Le workflow dépendait d'un runner ARM64 auto-hébergé indisponible | Avant tout déploiement physique, vérifier la disponibilité du runner Raspberry et utiliser l'accès distant de secours pour le rétablir |

## Critères de sortie d'un correctif Raspberry

Le statut `VALIDÉ` exige simultanément :

- le bon commit présent sur le Raspberry ;
- les fichiers réellement installés aux chemins exécutés ;
- les services et processus attendus actifs ;
- les contrôles fonctionnels réussis ;
- pour l'interface, une preuve visuelle du résultat ;
- un test de non-régression ajouté si l'incident révélait une faiblesse de contrôle.

Si l'un de ces éléments manque, le statut reste `NON VALIDÉ` même si GitHub Actions est vert.

## Principe de coût

Avant toute action susceptible de générer un coût variable, choisir dans cet ordre : local/gratuit, outils déjà inclus, automatisation légère, API payante, solution plus coûteuse uniquement si nécessaire. Le coût doit être annoncé en euros avant l'action quand il n'est pas négligeable.

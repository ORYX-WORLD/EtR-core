# Fabrique microSD EtR — architecture et règles d'exploitation

## Objectif

La fabrication d'une carte EtR doit être un processus transactionnel, observable et indépendant de la fenêtre graphique. Une fermeture de fenêtre, un redémarrage du bureau SPI ou une reconnexion VNC ne doit jamais interrompre la copie ni rendre son état ambigu.

## Architecture

- `etr-sd-factory.service` : interface graphique uniquement.
- `etr-sd-factory-worker.service` : moteur root indépendant, lancé à la demande.
- `etr_sd_factory_state.py` : état atomique persistant dans `/var/lib/etr-core/sd-factory-state.json`.
- `etr_sd_factory_worker.py` : verrou exclusif, fabrication, annulation et résultat final.
- `etr-sd-factory-cleanup` : démontage de secours limité aux points de montage de la fabrique.

La fenêtre peut être fermée et rouverte. Elle relit l'état persistant et reprend immédiatement le suivi du même travail.

## Machine d'états

`validating → ticket → unmounting → partitioning → formatting → mounting → copying_root → copying_boot → configuring → verifying → syncing → finalizing → ready`

États terminaux alternatifs :

- `failed` : erreur explicite, carte non validée ;
- `cancelled` : annulation utilisateur et démontage ;
- `interrupted` : moteur disparu sans validation finale.

Une carte n'est utilisable que si l'état final est `ready`, avec `verification=passed` et `ready_to_remove=true`.

## Garanties

1. Un seul travail à la fois grâce à `/run/lock/etr-sd-factory.lock`.
2. Le support système du banc est toujours exclu des cibles.
3. La taille et le chemin du support sont revérifiés juste avant l'effacement.
4. L'interface ne réalise aucune opération destructive elle-même.
5. La copie continue si l'interface est fermée ou redémarrée.
6. La progression, le débit et l'ETA sont persistés et affichés.
7. Les caches, profils Chromium, journaux et runner GitHub ne sont jamais copiés.
8. Le dépôt EtR est figé sur un commit cohérent avant son injection dans la carte.
9. Les identités, jetons, clés, machine-id, clés SSH et credentials du banc sont supprimés.
10. Le résultat final reste visible après fermeture de la fenêtre.
11. Toute annulation déclenche synchronisation et démontage avant de rendre la main.
12. Aucun message « prête » n'est émis avant la vérification et le démontage complets.

## Interaction utilisateur

- **PRÉPARER** : confirmation d'effacement, puis lancement du moteur.
- **ANNULER** : demande SIGINT au moteur, nettoyage contrôlé, état `cancelled`.
- **Fermer** : ferme uniquement le suivi ; la fabrication continue.
- Réouverture : rattachement automatique au travail actif ou au dernier résultat.

## Évolutions suivantes

Le clonage du système vivant reste une étape transitoire. La cible industrielle est une image de référence EtR versionnée et signée, construite en CI, puis écrite sur la carte avant injection du ticket, du Wi-Fi et des paramètres propres à l'installation. Cette évolution supprimera toute dépendance à l'état courant du banc pendant la fabrication.

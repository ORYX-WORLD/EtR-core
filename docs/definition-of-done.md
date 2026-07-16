# Definition of Done

Un changement est terminé lorsque :

1. le besoin est relié à une issue et les critères d'acceptation sont satisfaits ;
2. le code a été relu via une pull request et les contrôles CI sont verts ;
3. les tests proportionnés au risque sont documentés ;
4. `CHANGELOG.md`, les documents d'exploitation et les ADR sont à jour ;
5. la version, le commit et la cible sont identifiables dans l'artefact de déploiement ;
6. les secrets et données personnelles ne figurent ni dans Git ni dans les preuves ;
7. un retour arrière est possible et décrit pour tout changement opérationnel ;
8. la validation après déploiement est enregistrée.

Pour un changement matériel Raspberry, la preuve comprend au minimum l'état des
services, les ports attendus, le test de l'écran physique et le test du relais distant.

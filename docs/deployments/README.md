# Registre des déploiements

Les workflows génèrent un manifeste JSON immuable comme artefact GitHub Actions.
Il contient la version, le commit, la branche, la cible, l'horodatage UTC, l'acteur,
le run et le statut. Le registre ne doit jamais contenir de secret.

Les manifests téléchargés peuvent être validés avec
`schemas/deployment-manifest.schema.json` et sont conservés selon la politique de
rétention GitHub de l'organisation.

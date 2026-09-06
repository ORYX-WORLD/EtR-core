# Incident SD V1.1 — autorisation de fabrication

## Observation physique

Le 2026-09-06, la Fabrique SD V1.1 s'ouvre correctement sur l'écran physique du Raspberry. Le lecteur EC710 et la microSD 32 Go sont détectés comme `/dev/sda` (~29,1 Gio). La progression atteint l'étape « Autorisation sécurisée de fabrication… », puis l'interface affiche :

`Not authorized to perform operation`

## Conclusion provisoire

La détection USB et microSD est fonctionnelle. L'échec survient avant l'écriture de l'image, au moment d'obtenir les privilèges nécessaires aux opérations disque. Le correctif doit éviter toute dépendance à une autorisation graphique Polkit depuis la session opérateur et utiliser exclusivement le chemin privilégié prévu pour la Fabrique.

## Critère de validation

Le correctif n'est validé que lorsque, sur le Raspberry physique :

1. `/dev/sda` est détecté ;
2. la préparation démarre sans dialogue Polkit ;
3. la copie de l'image commence et la progression est visible ;
4. la fabrication se termine avec succès ;
5. un test de non-régression couvre l'absence de dépendance à `udisksctl`/Polkit dans le chemin critique.

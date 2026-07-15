# Comptes clients et enrôlement EtR

## Principes de sécurité

- L'adresse e-mail et Firebase Authentication identifient une personne.
- Le numéro de série identifie un Raspberry, mais n'est jamais considéré comme un secret.
- Un code d'activation aléatoire, valable 24 heures, associe l'équipement au compte.
- Un client peut disposer de plusieurs EtR via `memberships/{uid}/{installationId}`.
- Le mot de passe ouvre la session. Les droits sont stockés séparément et contrôlés par les règles Firebase.

## Rôles

| Rôle | Lecture | Alertes | Maintenance | Configuration | Utilisateurs | Logiciel EtR |
|---|---:|---:|---:|---:|---:|---:|
| Propriétaire | Oui | Oui | Oui | Oui | Oui | Non |
| Administrateur | Oui | Oui | Oui | Oui | Oui | Non |
| Installateur | Oui | Oui | Oui | Mise en service | Non | Non |
| Maintenance | Oui | Oui | Journal et commandes limitées | Non | Non | Non |
| Exploitant | Oui | Acquittement | Non | Non | Non | Non |
| Lecture seule | Oui | Non | Non | Non | Non | Non |
| Développeur ORYX | Oui | Oui | Oui | Oui | Support | Mise à jour |

Le rôle `Développeur ORYX` est un droit global interne porté par une custom claim. Les rôles clients sont attribués installation par installation.

## Parcours automatisé

1. Au démarrage, la passerelle lit le numéro de série matériel et envoie une demande d'enrôlement HTTPS.
2. ORYX génère un code d'activation à usage unique depuis un compte interne.
3. Le client crée son compte, vérifie son e-mail et saisit le numéro de série et le code.
4. `claimEtr` rattache l'installation au client avec le rôle propriétaire.
5. Le Raspberry échange le même code contre un jeton technique Firebase et conserve uniquement son jeton de renouvellement local.
6. Le client peut inviter d'autres utilisateurs et leur attribuer un rôle pour cet EtR.

## Compte d'essai

Le premier compte d'essai utilise `amotard.oryx@gmail.com`. Aucun mot de passe n'est enregistré dans GitHub. Une fois Email/Password activé dans Firebase Authentication, utiliser le bouton **Créer le compte**, puis vérifier l'adresse e-mail. Le rattachement de l'EtR nécessite ensuite son numéro de série réel et un code d'activation généré par ORYX.

## Variables Raspberry

```ini
FIREBASE_API_KEY=...
FIREBASE_DATABASE_URL=https://...firebasedatabase.app
FIREBASE_ENROLLMENT_URL=https://europe-west1-oryx-froid-industriel.cloudfunctions.net/deviceEnrollment
ETR_ACTIVATION_CODE=
```

Sans code, la passerelle envoie seulement une demande d'enrôlement. Après génération du code, renseigner `ETR_ACTIVATION_CODE`, redémarrer le service et vérifier que `/var/lib/etr-core/firebase-auth.json` a été créé avec les permissions `0600`.

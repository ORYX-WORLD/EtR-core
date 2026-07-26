# Enrôlement sécurisé d'un EtR

## Objectif

Permettre la première mise en service d'un Raspberry Pi EtR sans ordinateur, sans mot de passe technique partagé et sans intervention manuelle d'ORYX dans Firebase.

Le numéro de série matériel identifie l'équipement mais n'est jamais traité comme un secret. L'autorisation repose sur un code temporaire aléatoire affiché physiquement sur l'écran de l'EtR.

## Parcours complet

```text
Raspberry neuf
    │ POST /api/enrollment/request
    ▼
Passerelle Cloud Run
    │ enregistre uniquement des empreintes SHA-256
    ▼
Code temporaire affiché sur l'écran EtR
    │
    ▼
Client connecté et e-mail Firebase vérifié
    │ POST /api/enrollment/claim
    ▼
Membership owner + métadonnées installation
    │
    ▼
Raspberry échange une seule fois le code
    │ POST /api/enrollment/exchange
    ▼
Identité Firebase technique + refresh token local 0600
```

## Propriétés de sécurité

- code Crockford Base32 de 20 caractères, soit exactement 100 bits ;
- groupes de cinq caractères pour la saisie tactile : `XXXXX-XXXXX-XXXXX-XXXXX` ;
- normalisation manuelle des caractères ambigus `O/0` et `I/L/1` ;
- expiration après 24 heures ;
- huit tentatives incorrectes maximum par demande ;
- limitation complémentaire par adresse IP sur la passerelle ;
- code et jeton de rotation stockés dans Firebase uniquement sous forme de hash SHA-256 ;
- jeton de rotation connu seulement du Raspberry et conservé en local avec le mode `0600` ;
- compte client Firebase obligatoirement authentifié et adresse e-mail vérifiée ;
- claim transactionnel et échange verrouillé à usage unique ;
- identité technique déterministe liée à l'empreinte du numéro de série ;
- aucun mot de passe technique généré ou partagé ;
- refresh token stocké dans `/var/lib/etr-core/firebase-auth.json` avec le mode `0600` ;
- suppression locale du code après émission de l'identité technique.

## Endpoints de la passerelle

| Méthode | Route | Authentification | Usage |
|---|---|---|---|
| POST | `/api/enrollment/request` | aucune, rate limit | créer ou renouveler de manière autorisée une demande physique |
| POST | `/api/enrollment/claim` | Bearer Firebase utilisateur | associer l'installation au compte client vérifié |
| POST | `/api/enrollment/exchange` | code physique valide | émettre une fois un custom token pour l'identité technique |
| POST | `/api/enrollment` | selon `action` | compatibilité du bridge Raspberry |

La route `/healthz` expose `enrollment: "v1"` afin que le déploiement Cloud Run prouve que le backend actif comprend ce protocole.

## Données Firebase

```text
enrollmentRequests/{serialHash}
    version
    serialHash
    installationId
    hostname
    status
    attempts
    codeHash
    rotationTokenHash
    createdAt
    expiresAt
    ownerUid / claimedAt après claim
    deviceUid / completedAt après échange

deviceAccess/{deviceUid}
    installationId

memberships/{ownerUid}/{installationId}
    role: owner
    active: true

installations/{installationId}/metadata
    owner_uid
    owner_email
    device_uid
    device_fingerprint
    enrolled_at
```

Les clients ne lisent ni n'écrivent directement `enrollmentRequests` ou `deviceAccess`. Ces branches restent refusées par les règles Realtime Database ; seule la passerelle Admin les manipule.

## État local Raspberry

Avant l'association :

```text
/var/lib/etr-core/enrollment.json
```

Contient le code affichable, le jeton privé de rotation et l'expiration. Ce fichier est lisible uniquement par `oryx`.

Après l'association :

```text
/var/lib/etr-core/firebase-auth.json
```

Contient le refresh token Firebase de l'identité technique. Le fichier d'enrôlement est supprimé.

L'API locale `/api/v1/enrollment` ne retourne jamais le jeton de rotation. Elle ne fournit au dashboard que le code d'activation, le statut, l'identifiant d'installation et l'expiration.

## Déploiement et preuves

Le workflow Cloud Run :

1. installe les dépendances Node ;
2. construit noVNC ;
3. exécute les tests d'enrôlement ;
4. déploie la passerelle ;
5. contrôle `/healthz` ;
6. contrôle qu'une demande invalide retourne HTTP 400 ;
7. versionne `.github/deployment/gateway-last-deploy.json`.

Le workflow Raspberry :

1. installe `etr-firebase-bridge.service` depuis GitHub ;
2. impose l'utilisateur `oryx` ;
3. contrôle les permissions `0640` et `0600` ;
4. vérifie `/api/v1/enrollment` ;
5. vérifie le panneau tactile ;
6. versionne le statut d'enrôlement dans `.github/deployment/etr-last-deploy.txt`.

## Révocation et réaffectation

Une réaffectation ne doit pas être réalisée en modifiant simplement un membership. La procédure devra :

1. désactiver ou supprimer l'identité technique Firebase ;
2. supprimer `deviceAccess/{deviceUid}` ;
3. révoquer les refresh tokens ;
4. archiver l'ancien propriétaire et les droits ;
5. créer une nouvelle demande d'enrôlement physique.

Cette procédure de réaffectation reste une fonction d'administration ORYX à implémenter avant commercialisation multisite.

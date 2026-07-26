# Enrôlement sécurisé d'un EtR

## Objectif

Permettre la première mise en service d'un Raspberry Pi EtR sans ordinateur, sans mot de passe technique partagé et sans intervention manuelle d'ORYX dans Firebase.

Le numéro de série identifie l'équipement mais n'est pas un secret. La preuve de possession repose sur une paire Ed25519 générée sur le Raspberry : la clé privée reste locale en mode `0600` et la clé publique est enregistrée par GitHub Actions OIDC.

## Parcours complet

```text
Raspberry neuf
    │ génération locale Ed25519
    │ enregistrement de la clé publique par GitHub OIDC
    │ POST signé /api/enrollment/request
    ▼
Passerelle Cloud Run
    │ code Crockford Base32 100 bits, hashé côté cloud
    ▼
Code temporaire affiché sur l'écran EtR
    │
    ▼
Client connecté avec adresse Firebase vérifiée
    │ POST /api/enrollment/claim
    ▼
Membership owner + métadonnées installation
    │
    ▼
Raspberry signe l'échange du code
    │ POST /api/enrollment/exchange
    ▼
Session Firebase technique directe
    │ ID token + refresh token sur TLS, vers le Raspberry authentifié
    ▼
Refresh token local en mode 0600
```

## Propriétés de sécurité

- code Crockford Base32 de 20 caractères, soit exactement 100 bits ;
- affichage `XXXXX-XXXXX-XXXXX-XXXXX` ;
- expiration après 24 heures ;
- huit tentatives incorrectes maximum et limitation par adresse IP ;
- code et jeton de rotation stockés dans Firebase uniquement sous forme de SHA-256 ;
- demande et échange signés Ed25519 avec timestamp et nonce anti-rejeu ;
- compte client authentifié et adresse e-mail vérifiée ;
- claim transactionnel et échange verrouillé à usage unique ;
- UID technique déterministe lié à l'empreinte du Raspberry ;
- compte technique interne associé à une adresse réservée `@devices.oryx.invalid` ;
- mot de passe technique aléatoire de 384 bits, créé uniquement en mémoire par la passerelle, jamais renvoyé ni enregistré par EtR ;
- anciens refresh tokens révoqués avant réémission ;
- claims `etrDevice` et `installationId` ajoutés avant la connexion technique ;
- ID token et refresh token remis directement au Raspberry par la requête TLS signée ;
- refresh token stocké dans `/var/lib/etr-core/firebase-auth.json` avec le mode `0600` ;
- suppression locale du code après écriture réussie des jetons.

Cette méthode ne dépend plus de `iam.serviceAccounts.signBlob` ni de `createCustomToken`. Elle conserve une compatibilité transitoire côté bridge pour lire un ancien `customToken`, mais la passerelle v1 émet désormais une session directe.

## Endpoints de la passerelle

| Méthode | Route | Authentification | Usage |
|---|---|---|---|
| POST | `/api/enrollment/bootstrap` | GitHub Actions OIDC | enregistrer la clé publique Ed25519 |
| POST | `/api/enrollment/request` | signature Ed25519 | créer ou renouveler une demande physique |
| POST | `/api/enrollment/claim` | Bearer Firebase utilisateur vérifié | associer l'installation au compte client |
| POST | `/api/enrollment/exchange` | signature Ed25519 + code valide | émettre une fois la session Firebase technique |
| POST | `/api/enrollment/session-health` | GitHub Actions OIDC | prouver l'émission réelle d'une session sans exposer ses jetons |
| POST | `/api/enrollment` | selon `action` | compatibilité du bridge Raspberry |

## Données Firebase

```text
deviceBootstrap/{serialHash}
    installationId
    publicKey
    publicKeyFingerprint
    workflowRunId / workflowSha

enrollmentRequests/{serialHash}
    installationId
    status
    attempts
    codeHash
    rotationTokenHash
    expiresAt
    ownerUid / claimedAt
    deviceUid / completedAt

deviceAccess/{deviceUid}
    installationId

memberships/{ownerUid}/{installationId}
    role: owner
    active: true
```

Les clients ne lisent ni n'écrivent directement `deviceBootstrap`, `enrollmentRequests` ou `deviceAccess`.

## État local Raspberry

Avant l'association :

```text
/var/lib/etr-core/bootstrap-private.pem
/var/lib/etr-core/bootstrap-public.pem
/var/lib/etr-core/enrollment.json
```

Après l'association :

```text
/var/lib/etr-core/firebase-auth.json
```

L'API locale `/api/v1/enrollment` ne retourne jamais la clé privée ou le jeton de rotation. Le dashboard reçoit uniquement le code affichable, le statut, l'identifiant d'installation et l'expiration.

## Preuves de déploiement

Le workflow Cloud Run :

1. exécute les tests Node ;
2. construit et démarre l'image Docker exacte ;
3. déploie Cloud Run avec l'API key Firebase publique configurée ;
4. vérifie les routes `request` et `bootstrap` ;
5. demande un jeton GitHub OIDC ;
6. appelle `/api/enrollment/session-health` ;
7. crée, connecte, révoque puis supprime une identité de santé ;
8. exige `deviceSessionIssuance=true` dans `gateway-last-deploy.json`.

Le workflow Raspberry vérifie le bridge sous `oryx`, les permissions locales, le panneau tactile, l'enregistrement OIDC de la clé publique et le statut d'enrôlement.

## Révocation et réaffectation

Une réaffectation contrôlée doit :

1. révoquer les refresh tokens ;
2. désactiver ou supprimer l'identité technique ;
3. supprimer `deviceAccess/{deviceUid}` ;
4. désactiver les anciens memberships ;
5. archiver la demande précédente ;
6. créer une nouvelle demande physique et, si nécessaire, renouveler la paire Ed25519.

# Audit de la configuration e-mail EtR

Dernière mise à jour : 2026-08-02

## But

Lire la configuration non sensible d'Identity Platform utilisée par les comptes EtR afin de préparer une amélioration de délivrabilité sans élargir les droits de la passerelle Cloud Run et sans perturber le parcours d'authentification déjà validé.

## Constat de production

Deux e-mails de vérification réels ont été reçus dans Gmail avec :

```text
Expéditeur : noreply@oryx-froid-industriel.firebaseapp.com
Objet       : Verify your email for oryx-froid-industriel
Lien        : https://oryx-froid-industriel.firebaseapp.com/__/auth/action?...
Classement  : Spam
```

L'interface EtR avertit déjà l'utilisateur, mais cette mesure ne modifie ni l'expéditeur, ni l'authentification du domaine, ni le modèle réellement envoyé.

## Audit automatisé

Le workflow `.github/workflows/etr-identity-email-config-audit.yml` utilise l'identité WIF déjà employée par EtR-core et demande uniquement la lecture de :

- la méthode d'envoi e-mail ;
- la langue par défaut ;
- le callback des actions ;
- l'état d'un éventuel domaine personnalisé ;
- la présence d'un SMTP sans publier son mot de passe ;
- les paramètres non sensibles des modèles de vérification et de mot de passe ;
- les domaines autorisés et le site Hosting par défaut.

Le rapport est assaini et enregistré dans :

```text
.github/deployment/etr-identity-email-config-last.json
```

Le premier audit WIF a confirmé que l'authentification Google Cloud fonctionne, mais que l'identité de déploiement ne possède pas `firebaseauth.configs.get`.

## Test des possibilités d'automatisation IAM

Le workflow `.github/workflows/etr-email-iam-capabilities.yml` appelle `projects.testIamPermissions`, opération en lecture seule, pour déterminer si l'identité WIF possède déjà :

```text
firebaseauth.configs.get
firebaseauth.configs.update
firebaseauth.users.get
resourcemanager.projects.getIamPolicy
resourcemanager.projects.setIamPolicy
iam.roles.get
iam.roles.create
iam.roles.update
serviceusage.services.enable
```

La preuve est enregistrée dans :

```text
.github/deployment/etr-email-iam-capabilities-last.json
```

Le workflow ne crée aucun rôle, ne modifie aucune politique IAM et ne publie pas l'adresse du compte de service ; seule une empreinte courte du principal est conservée.

## Décision après lecture

### Configuration lisible

Préparer un patch séparé avec `updateMask` limité aux seuls champs approuvés :

- langue française ;
- expéditeur affiché `EtR – ORYX` ;
- objet et corps français ;
- adresse de réponse ORYX ;
- gestionnaire d'action personnalisé sur le site EtR.

La méthode d'envoi, le domaine personnalisé et le SMTP restent inchangés lors de ce premier patch.

### Configuration non lisible, sans droit de gérer IAM

Ne pas attribuer automatiquement un rôle administrateur global. Utiliser l'une de ces voies :

- attribuer manuellement le rôle de lecture `roles/identityplatform.viewer` ;
- créer manuellement un rôle personnalisé contenant uniquement `firebaseauth.configs.get` ;
- relever les champs nécessaires dans la console Firebase / Identity Platform.

### Configuration non lisible, mais gestion IAM techniquement possible

Ne pas modifier immédiatement la politique. Produire d'abord la preuve des permissions, puis préparer une opération distincte, réversible et limitée à la lecture. Aucun rôle d'administration Identity Platform ne doit être attribué pour un simple audit.

## Garde-fous

- aucun secret dans GitHub ;
- aucune mutation IAM dans les workflows d'audit ;
- aucune modification SMTP ou DNS dans l'audit ;
- preuve sans corps complet des modèles ;
- patch de modèle séparé d'une future migration de domaine d'envoi ;
- test réel Gmail et seconde messagerie avant clôture.

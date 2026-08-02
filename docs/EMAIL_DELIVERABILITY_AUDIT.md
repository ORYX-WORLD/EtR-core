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

Le workflow ne met à jour aucune configuration.

## Décision après lecture

### Configuration lisible

Préparer un patch séparé avec `updateMask` limité aux seuls champs approuvés :

- langue française ;
- expéditeur affiché `EtR – ORYX` ;
- objet et corps français ;
- adresse de réponse ORYX ;
- gestionnaire d'action personnalisé sur le site EtR.

La méthode d'envoi, le domaine personnalisé et le SMTP restent inchangés lors de ce premier patch.

### Configuration non lisible

Ne pas attribuer automatiquement un rôle administrateur global. Documenter la permission manquante `firebaseauth.configs.get`, puis utiliser soit :

- une permission de lecture minimale accordée à l'identité de déploiement ;
- soit la console Firebase / Identity Platform pour relever les champs nécessaires.

## Garde-fous

- aucun secret dans GitHub ;
- aucune mutation IAM automatique ;
- aucune modification SMTP ou DNS dans l'audit ;
- preuve sans corps complet des modèles ;
- patch de modèle séparé d'une future migration de domaine d'envoi ;
- test réel Gmail et seconde messagerie avant clôture.

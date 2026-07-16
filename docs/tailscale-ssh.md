# Administration du Raspberry EtR avec Tailscale SSH

Tailscale SSH fournit un canal d’administration distinct de l’écran VNC EtR.
Il permet d’ouvrir un terminal et de transférer des fichiers sans publier le
port 22 sur Internet et sans ouvrir de port sur la box.

## Installation sur le Raspberry

Après avoir récupéré la dernière version du dépôt :

```bash
cd /home/oryx/EtR-core
git pull --ff-only origin main
sudo bash scripts/setup_tailscale_ssh.sh
```

Le script affiche une URL Tailscale lors de la première exécution. Ouvrez cette
URL sur le PC et validez l’ajout du Raspberry au compte Tailscale choisi. Aucune
clé d’authentification Tailscale n’est enregistrée dans le dépôt.

## Connexion depuis Windows

1. Installez Tailscale sur Windows depuis le site officiel.
2. Connectez Windows au même réseau Tailscale que le Raspberry.
3. Dans PowerShell, utilisez l’adresse affichée par le script :

```powershell
ssh oryx@100.x.y.z
```

Pour transférer un fichier :

```powershell
scp .\fichier.txt oryx@100.x.y.z:/home/oryx/
```

Le nom MagicDNS peut aussi fonctionner si cette fonction est active sur le
réseau Tailscale :

```powershell
ssh oryx@etr-core
```

## Contrôle et désactivation

Sur le Raspberry :

```bash
tailscale status
tailscale ip -4
sudo tailscale set --ssh=false
```

`tailscale set --ssh=false` désactive uniquement Tailscale SSH. Pour retirer
complètement le Raspberry du réseau Tailscale :

```bash
sudo tailscale logout
```

Si une politique d’accès Tailscale personnalisée remplace la politique par
défaut, elle doit autoriser l’utilisateur concerné à se connecter en SSH à ce
Raspberry. Le script ne détend pas automatiquement les règles du réseau.

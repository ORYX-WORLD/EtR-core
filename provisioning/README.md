# Fabrication automatisée des microSD EtR

Chaque Raspberry EtR utilise une microSD, mais pas une image manuelle propre à
chaque client. La chaîne de fabrication écrit la même image Raspberry Pi OS
Lite 64 bits, puis le premier démarrage dérive une identité unique du numéro
matériel du Raspberry.

## Résultat

- effacement intégral de l'ancienne carte par réécriture de l'image ;
- contrôle anti-erreur sur le disque Windows choisi ;
- SSH activé uniquement par clé, sans mot de passe partagé ;
- utilisateur technique `oryx` créé automatiquement ;
- nom d'hôte et identifiant d'installation `etr-<8 caractères du numéro matériel>` ;
- clonage du dépôt officiel et exécution de `setup_etr.sh` dès qu'Ethernet est disponible ;
- nouvelle tentative automatique si Internet est momentanément indisponible ;
- configuration Wi-Fi ultérieure par le portail tactile EtR, sans PC.

## Préparation du poste Windows

1. Installer Raspberry Pi Imager et OpenSSH Client.
2. Télécharger une image officielle Raspberry Pi OS Lite 64 bits compatible avec
   Raspberry Pi 3 et Pi 4.
3. Cloner ce dépôt sur le PC.
4. Brancher l'adaptateur microSD/USB.
5. Ouvrir PowerShell **en administrateur**.
6. Identifier la carte avec `Get-Disk`.

## Commande

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\provisioning\windows\New-EtrMicroSD.ps1 `
  -ImagePath "C:\Images\raspios-lite-arm64.img.xz" `
  -DiskNumber 3
```

Le numéro de disque est volontairement obligatoire. Le script affiche le
modèle, le bus et la capacité puis exige la saisie `EFFACER-3`. Il refuse le
disque système, les supports de plus de 256 Go et les bus autres que USB/SD/MMC.

Il n'est pas nécessaire de formater la carte avant : Raspberry Pi Imager
réécrit sa table de partitions et son contenu, puis vérifie l'écriture.

## Premier démarrage

Insérer la carte, brancher Ethernet et mettre le Raspberry sous tension. Le
premier démarrage crée l'identité et le service de provisionnement, puis
redémarre. L'installation EtR continue automatiquement en ligne et peut durer
plusieurs minutes.

Après installation, le Wi-Fi du site se configure depuis l'écran tactile EtR.
La clé privée SSH reste sur le PC ayant fabriqué la carte ; seule sa clé publique
est copiée sur le Raspberry.


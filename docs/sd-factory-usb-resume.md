# Fabrique microSD — reprise contrôlée après perte USB

## Objectif

La fabrique EtR doit pouvoir continuer une copie lorsque le lecteur microSD perd
momentanément la communication USB, sans déclarer prête une carte potentiellement
corrompue.

La stratégie retenue est une **reprise contrôlée sur incident USB**. Ce n'est pas
une simple pause du processus : toute perte du support déclenche un contrôle
d'intégrité avant la reprise.

## Périmètre

La reprise automatique couvre les phases de copie `rsync` :

- copie du système racine ;
- copie de la partition de démarrage ;
- installation du dépôt EtR figé dans la carte.

Une perte USB pendant l'effacement, le partitionnement ou le formatage reste un
échec terminal. Ces étapes doivent repartir de zéro car aucun système de fichiers
fiable n'existe encore.

## Cycle de reprise

1. La capacité du disque parent est contrôlée chaque seconde.
2. Dès que la capacité disparaît ou change, le groupe de processus `rsync` est
   arrêté pour empêcher de nouvelles écritures utilisateur.
3. La fabrique passe dans l'état `paused_usb` et attend au maximum 90 secondes.
4. Le même support est recherché par :
   - taille du disque ;
   - PTUUID de la table de partitions ;
   - PARTUUID ;
   - UUID du système de fichiers ;
   - numéro de partition.
5. Le support doit rester stable pendant cinq secondes consécutives.
6. Le montage devenu obsolète est détaché.
7. Le système de fichiers est contrôlé hors montage :
   - `e2fsck -p -f` pour ext2/ext3/ext4 ;
   - `fsck.vfat -a` pour FAT/VFAT.
8. La partition est remontée au même point de montage.
9. `rsync` est relancé avec :
   - `--partial` ;
   - `--partial-dir=.etr-rsync-partial` ;
   - `--no-whole-file`.
10. Les fichiers déjà valides sont conservés ; le fichier incomplet est repris à
    partir de sa copie partielle ou recalculé par blocs.
11. La progression globale reste monotone dans l'interface.

## Limites de sécurité

- deux reprises USB maximum par fabrication ;
- 90 secondes maximum par attente de reconnexion ;
- abandon si le support revenu ne possède pas les mêmes identifiants ;
- abandon si `fsck` ne peut pas corriger automatiquement le système de fichiers ;
- abandon à la troisième disparition ;
- aucun état `ready` avant vérification, synchronisation et démontage final.

Les valeurs peuvent être ajustées par variables d'environnement :

```text
ETR_SD_MAX_USB_RECOVERIES=2
ETR_SD_USB_RECONNECT_TIMEOUT_SECONDS=90
ETR_SD_USB_STABLE_SECONDS=5
ETR_SD_RSYNC_BWLIMIT_KB=2048
```

## États affichés

```text
paused_usb
checking_filesystem
resuming_copy
copying_root / copying_boot
```

Une carte en échec peut être `safe_to_remove=true` si elle est démontée, mais
`ready_to_remove` reste toujours faux. Seul l'état final `ready`, accompagné de
`verification=passed`, autorise son utilisation dans un nouvel EtR.

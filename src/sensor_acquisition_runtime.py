#!/usr/bin/env python3
"""Point d'entrée matériel EtR pour le banc ADS1263 partagé avec l'écran.

GPIO17 appartient au tactile et GPIO22 est piloté par le noyau comme SPI0 CS2.
Le service root prépare RESET/GPIO18 à l'état haut avant ce processus. Le
convertisseur est ensuite remis à zéro par sa commande SPI, sans ouvrir lgpio
depuis le processus non privilégié.
"""

from __future__ import annotations

try:  # Import comme module de paquet pendant les tests.
    from . import sensor_acquisition as acquisition
    from .ads1263 import ADS1263
except ImportError:  # Exécution directe du fichier par systemd.
    import sensor_acquisition as acquisition
    from ads1263 import ADS1263


class SoftwareResetADS1263(ADS1263):
    """ADS1263 utilisant le chip-select noyau et le reset logiciel SPI."""

    def __init__(self, *args, **kwargs):
        kwargs["manual_chip_select"] = False
        kwargs["use_data_ready_gpio"] = False
        kwargs["use_hardware_reset_gpio"] = False
        super().__init__(*args, **kwargs)


# `acquire_once` conserve la fabrique dans ses valeurs par défaut. La remplacer
# ici limite le changement au point d'entrée matériel sans modifier les outils
# de calcul et leurs tests.
acquisition.acquire_once.__defaults__ = (SoftwareResetADS1263,)


if __name__ == "__main__":
    raise SystemExit(acquisition.main())

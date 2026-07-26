# Contrat de télémétrie EtR 1.0

Le producteur d'acquisition écrit atomiquement le fichier `/var/lib/etr-core/telemetry.json`. Il doit écrire un fichier temporaire puis le renommer afin que l'API ne lise jamais un JSON partiel.

```json
{
  "updated_at": "2026-07-26T08:00:00+00:00",
  "source": "modbus-main",
  "measurements": {
    "pressure_bar": 31.25,
    "temperature_c": -8.6
  },
  "states": {
    "compressor_on": true,
    "compressor_state": "running"
  },
  "alerts": [
    {
      "code": "HP_HIGH",
      "severity": "warning",
      "message": "Pression haute à contrôler"
    }
  ]
}
```

## Règles

- `updated_at` est une date UTC ISO 8601.
- `measurements` contient uniquement des nombres accompagnés d'une unité dans leur nom ou dans le futur registre de points.
- `states` contient les états discrets normalisés.
- `alerts` contient au maximum les alarmes actives utiles à l'exploitation.
- Une donnée absente reste absente : aucune valeur de pression, température ou état compresseur ne doit être simulée.
- Le mapping Modbus/GPIO/série est propre à une configuration d'installation et doit être versionné séparément de ses secrets d'accès.

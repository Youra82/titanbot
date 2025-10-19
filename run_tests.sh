#!/bin/bash
# Dieses Skript führt das komplette Test-Sicherheitsnetz aus.
echo "--- Starte JaegerBot-Sicherheitsnetz ---"

# Aktiviere die virtuelle Umgebung
source .venv/bin/activate

# Führe pytest aus. -v für mehr Details, -s um print() Ausgaben anzuzeigen.
python3 -m pytest -v -s

# Deaktiviere die Umgebung wieder
deactivate

echo "--- Sicherheitscheck abgeschlossen ---"

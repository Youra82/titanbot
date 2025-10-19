#!/bin/bash
# Dieses Skript führt das komplette Test-Sicherheitsnetz aus.
# *** TITEL GEÄNDERT ***
echo "--- Starte TitanBot-Sicherheitsnetz ---"

# Aktiviere die virtuelle Umgebung
if [ ! -f ".venv/bin/activate" ]; then
    echo "Fehler: Virtuelle Umgebung nicht gefunden. Bitte install.sh ausführen."
    exit 1
fi
source .venv/bin/activate

# Führe pytest aus. -v für mehr Details, -s um print() Ausgaben anzuzeigen.
# ACHTUNG: Die Tests müssen erst für TitanBot geschrieben werden!
echo "WARNUNG: Führe pytest aus, aber die Tests für TitanBot müssen erst implementiert werden."
if python3 -m pytest -v -s; then
    echo "Pytest erfolgreich durchgelaufen (möglicherweise keine Tests gefunden)."
else
    echo "Pytest fehlgeschlagen."
fi


# Deaktiviere die Umgebung wieder
deactivate

echo "--- Sicherheitscheck abgeschlossen ---"

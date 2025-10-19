
#!/bin/bash

# Überprüfen, ob ein Dateiname übergeben wurde
if [ -z "$1" ]; then
    echo "Fehler: Du musst den Namen der CSV-Datei angeben."
    echo "Beispiel: bash show_chart.sh optimal_portfolio_equity.csv"
    exit 1
fi

# Aktiviere die virtuelle Umgebung
source .venv/bin/activate

# Führe das Python-Skript aus und übergebe den Dateinamen
python3 generate_and_send_chart.py "$1"

# Deaktiviere die Umgebung wieder
deactivate

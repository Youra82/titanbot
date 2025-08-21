#!/bin.sh
# Dieses Skript installiert den Bot und seine Abhängigkeiten.
# Es kann von überall auf dem Server ausgeführt werden.

# Bricht das Skript bei Fehlern sofort ab, um eine halbfertige Installation zu verhindern
set -e

# Finde das Verzeichnis, in dem das Skript selbst liegt (das Hauptverzeichnis des Bots)
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)

echo ">>> Starte Installation im Verzeichnis: $SCRIPT_DIR"

echo ">>> Server-Paketlisten werden aktualisiert..."
sudo apt-get update

echo ">>> Wichtige Python-Pakete (pip, venv) werden installiert..."
sudo apt-get install python3-pip python3-venv -y

# Definiere den genauen Pfad zum Code-Verzeichnis
CODE_DIR="$SCRIPT_DIR/code"

echo ">>> Virtuelle Umgebung wird in '$CODE_DIR' erstellt..."

# Lösche eine eventuell alte oder fehlerhafte Umgebung
rm -rf "$CODE_DIR/.venv"

# Erstelle die neue, saubere virtuelle Umgebung
python3 -m venv "$CODE_DIR/.venv"

echo ">>> Virtuelle Umgebung wird aktiviert und alle Pakete werden installiert..."

# Führe die Installation innerhalb einer Sub-Shell aus, um die Umgebung sauber zu halten
(
  source "$CODE_DIR/.venv/bin/activate"
  pip install --upgrade pip
  pip install -r "$SCRIPT_DIR/requirements.txt"
)

echo ""
echo "--------------------------------------------------------"
echo "✔ Installation erfolgreich abgeschlossen!"
echo "Die virtuelle Umgebung ist jetzt in '$CODE_DIR/.venv' bereit."
echo "--------------------------------------------------------"

#!/bin/bash

# --- Not-Aus-Schalter ---
set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}======================================================="
# *** TITEL GEÄNDERT ***
echo "         TitanBot Installations-Skript"
echo "=======================================================${NC}"

# --- System-Abhängigkeiten installieren ---
echo -e "\n${YELLOW}1/4: Aktualisiere Paketlisten und installiere System-Abhängigkeiten...${NC}"
# Prüfe, ob sudo benötigt wird (z.B. nicht in Docker als root)
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; else SUDO=""; fi
$SUDO apt-get update
# *** Python 3.12 Annahme beibehalten, ggf. anpassen ***
$SUDO apt-get install -y python3.12 python3.12-venv git curl jq 
echo -e "${GREEN}✔ System-Abhängigkeiten installiert.${NC}"

# --- Python Virtuelle Umgebung einrichten ---
echo -e "\n${YELLOW}2/4: Erstelle eine isolierte Python-Umgebung (.venv)...${NC}"
python3.12 -m venv .venv # Explizit Version nutzen
echo -e "${GREEN}✔ Virtuelle Umgebung wurde erstellt.${NC}"

# --- Python-Bibliotheken installieren ---
echo -e "\n${YELLOW}3/4: Aktiviere die virtuelle Umgebung und installiere Python-Bibliotheken...${NC}"
source .venv/bin/activate
pip install --upgrade pip
# Prüfe ob requirements.txt existiert
if [ ! -f "requirements.txt" ]; then
    echo -e "${RED}FEHLER: requirements.txt nicht gefunden!${NC}"
    deactivate
    exit 1
fi
pip install -r requirements.txt
echo -e "${GREEN}✔ Alle Python-Bibliotheken wurden erfolgreich installiert.${NC}"
deactivate

# --- Symlink für /home/ubuntu/titanbot erstellen (falls als root installiert) ---
CURRENT_DIR=$(pwd)
UBUNTU_HOME="/home/ubuntu"
BOT_NAME="titanbot"

if [ "$(id -u)" -eq 0 ] && [ -d "$UBUNTU_HOME" ]; then
    TARGET_LINK="$UBUNTU_HOME/$BOT_NAME"
    
    if [ "$CURRENT_DIR" != "$TARGET_LINK" ]; then
        if [ -L "$TARGET_LINK" ]; then
            echo -e "${YELLOW}  → Symlink $TARGET_LINK existiert bereits${NC}"
        elif [ -d "$TARGET_LINK" ]; then
            echo -e "${YELLOW}  → Lösche existierendes Verzeichnis $TARGET_LINK und erstelle Symlink...${NC}"
            rm -rf "$TARGET_LINK"
            ln -s "$CURRENT_DIR" "$TARGET_LINK"
            echo -e "${GREEN}✔ Symlink erstellt: $TARGET_LINK -> $CURRENT_DIR${NC}"
        else
            ln -s "$CURRENT_DIR" "$TARGET_LINK"
            echo -e "${GREEN}✔ Symlink erstellt: $TARGET_LINK -> $CURRENT_DIR${NC}"
        fi
    fi
fi

# --- Abschluss ---
echo -e "\n${YELLOW}4/4: Setze Ausführungsrechte für alle .sh-Skripte...${NC}"
chmod +x *.sh

echo -e "\n${GREEN}======================================================="
echo "✅  Installation erfolgreich abgeschlossen!"
echo ""
# *** MELDUNGEN ANGEPASST ***
echo "Nächste Schritte:"
echo "  1. Erstelle/Bearbeite die 'secret.json' Datei mit deinen API-Keys."
echo "     ( nano secret.json )"
echo "  2. Führe die Optimierungs-Pipeline aus, um SMC-Strategien zu finden:"
echo "     ( ./run_pipeline.sh )"
echo "  3. Bearbeite 'settings.json', um die gewünschten Strategien zu aktivieren."
echo "     ( nano settings.json )"
echo "  4. Richte einen Cronjob ein, um 'master_runner.py' regelmäßig zu starten."
echo "     ( crontab -e )"
echo "  5. Starte den Live-Bot manuell (optional zum Testen):"
echo "     ( python3 master_runner.py )"
echo -e "=======================================================${NC}"

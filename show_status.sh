#!/bin/bash

# Farben für eine schönere Ausgabe definieren
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Hauptverzeichnis des Projekts bestimmen
PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)

# Funktion, um den Inhalt einer Datei formatiert auszugeben
show_file_content() {
    FILE_PATH=$1
    
    # Bestimme eine beschreibende Überschrift basierend auf dem Dateinamen/Pfad
    DESCRIPTION=$(basename "$FILE_PATH")

    if [ -f "${FILE_PATH}" ]; then
        echo -e "\n${BLUE}======================================================================${NC}"
        echo -e "${YELLOW}DATEI: ${DESCRIPTION}${NC}"
        echo -e "${CYAN}Pfad: ${PROJECT_ROOT}/${FILE_PATH#./}${NC}"
        echo -e "${BLUE}----------------------------------------------------------------------${NC}"
        
        # Spezielle Zensur-Logik nur für secret.json
        if [[ "$DESCRIPTION" == "secret.json" ]]; then
            echo -e "${YELLOW}HINWEIS: Sensible Daten in secret.json wurden zensiert.${NC}"
            sed -E 's/("apiKey"|"secret"|"password"|"bot_token"|"chat_id"|"sender_password"): ".*"/"\1": "[ZENSIERT]"/g' "${FILE_PATH}" | cat -n
        else
            cat -n "${FILE_PATH}"
        fi
        
        echo -e "${BLUE}======================================================================${NC}"
    else
        echo -e "\n${RED}WARNUNG: Datei nicht gefunden unter ${FILE_PATH}${NC}"
    fi
}

# --- ANZEIGE ALLER RELEVANTEN CODE-DATEIEN ---
echo -e "${BLUE}======================================================================${NC}"
echo "           Vollständige Code-Dokumentation des JaegerBot"
echo -e "${BLUE}======================================================================${NC}"

# Finde alle relevanten Dateien, ABER schließe secret.json vorerst aus.
# Speichere die Pfade in einem Array.
mapfile -t FILE_LIST < <(find . -path './.venv' -prune -o -path './secret.json' -prune -o \( -name "*.py" -o -name "*.sh" -o -name "*.json" -o -name "*.txt" -o -name ".gitignore" \) -print)

# Zeige zuerst alle anderen Dateien an
for filepath in "${FILE_LIST[@]}"; do
    show_file_content "$filepath"
done

# Zeige die secret.json als LETZTE Datei an
show_file_content "secret.json"

# --- ANZEIGE DER PROJEKTSTRUKTUR AM ENDE ---
echo -e "\n\n${BLUE}======================================================="
echo "            Aktuelle Projektstruktur"
echo -e "=======================================================${NC}"

# Eine Funktion, die eine Baumstruktur mit Standard-Tools emuliert
list_structure() {
    find . -path '*/.venv' -prune -o \
           -path '*/__pycache__' -prune -o \
           -path './.git' -prune -o \
           -path './artifacts/db' -prune -o \
           -path './artifacts/models' -prune -o \
           -path './logs' -prune -o \
           -maxdepth 4 -print | sed -e 's;[^/]*/;|____;g;s;____|; |;g'
}

list_structure

echo -e "${BLUE}=======================================================${NC}"

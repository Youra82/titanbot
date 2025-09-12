#!/bin/bash

# Bricht das Skript bei Fehlern sofort ab
set -e

SECRET_FILE="secret.json"
BACKUP_FILE="secret.json.bak"

echo "--- Sicheres Update wird ausgeführt (v3 - vollautomatisch, lokale Änderungen werden priorisiert) ---"

# Schritt 1: Backup der Keys erstellen
echo "1. Erstelle ein Backup von '$SECRET_FILE' nach '$BACKUP_FILE'..."
cp "$SECRET_FILE" "$BACKUP_FILE"

# Schritt 2: Lokale Änderungen sicher beiseite legen
echo "2. Lege alle lokalen Änderungen mit 'git stash' sicher beiseite..."
git stash push --include-untracked

# Schritt 3: Neuesten Stand von GitHub holen, lokale Änderungen priorisieren
echo "3. Hole die neuesten Updates von GitHub (lokale Änderungen werden priorisiert)..."
git fetch origin
git reset --hard origin/main    # remote Änderungen holen
git stash pop || true           # lokale Änderungen wieder einspielen

# Schritt 4: Backup wiederherstellen, um absolute Sicherheit zu garantieren
echo "4. Stelle den Inhalt von '$SECRET_FILE' aus dem Backup wieder her..."
cp "$BACKUP_FILE" "$SECRET_FILE"

echo "✅ Update erfolgreich abgeschlossen. Lokale Anpassungen bleiben erhalten."

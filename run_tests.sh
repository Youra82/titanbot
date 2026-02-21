#!/bin/bash
echo "--- Starte TitanBot-Sicherheitsnetz ---"

if [ ! -f ".venv/bin/activate" ]; then
    echo "Fehler: Virtuelle Umgebung nicht gefunden. Bitte install.sh ausführen."
    exit 1
fi
source .venv/bin/activate

# Windows-Kompatibilität: python3 existiert ggf. nicht im PATH
PYTHON_CMD=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
if [ -z "$PYTHON_CMD" ]; then
    echo "Fehler: Kein Python-Interpreter gefunden."
    exit 1
fi

echo "Führe Pytest aus (inkl. Live-Workflow-Test)..."
if "$PYTHON_CMD" -m pytest -v -s; then
    echo "Pytest erfolgreich durchgelaufen. Alle Tests bestanden."
    EXIT_CODE=0
else
    PYTEST_EXIT_CODE=$?
    if [ $PYTEST_EXIT_CODE -eq 5 ]; then
        echo "Pytest beendet: Keine Tests zum Ausführen gefunden."
        EXIT_CODE=0
    else
        echo "Pytest fehlgeschlagen (Exit Code: $PYTEST_EXIT_CODE)."
        EXIT_CODE=$PYTEST_EXIT_CODE
    fi
fi

deactivate
echo "--- Sicherheitscheck abgeschlossen ---"
exit $EXIT_CODE

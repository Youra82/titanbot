#!/bin/bash

# Ermittle das Verzeichnis, in dem sich das Skript befindet (/home/ubuntu/stbot/code)
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)

# Aktiviere die virtuelle Umgebung, die sich im selben Verzeichnis befindet
source "$SCRIPT_DIR/.venv/bin/activate"

# Führe das Python-Skript aus, das sich relativ zu diesem Skript befindet
python3 "$SCRIPT_DIR/strategies/envelope/run.py"

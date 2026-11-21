#!/bin/sh
# Sicheres Update: holt den Repo-Stand, behaelt secret.json und den Papier/Echtgeld-Schalter (settings.json echtgeld).
set -eu
cd "$(dirname "$0")"
# laufenden Takt nicht unterbrechen (offene Orders), Timer pausieren; Timer läuft am Ende (auch nach Fehler) wieder
trap 'sudo systemctl start titanbot.timer 2>/dev/null || true' EXIT
sudo systemctl stop titanbot.timer 2>/dev/null || true
N=0
while systemctl is-active --quiet titanbot.service 2>/dev/null; do
    N=$((N + 1)); [ "$N" -le 360 ] || { echo "titanbot-Takt läuft seit 30 min – Update abgebrochen, bitte prüfen" >&2; exit 1; }
    echo "warte auf laufenden titanbot-Takt ..."; sleep 5
done
if git ls-files --error-unmatch secret.json >/dev/null 2>&1 || test -n "$(git ls-files data/)"; then
    echo 'Abbruch: secret.json oder data/ ist im Git erfasst.' >&2; exit 1
fi
TMP=$(mktemp -d)
[ -f secret.json ] && cp -p secret.json "$TMP/secret.json"
[ -f settings.json ] && cp -p settings.json "$TMP/settings.json"
git fetch origin
git reset --hard origin/main
[ -f "$TMP/secret.json" ] && cp -p "$TMP/secret.json" secret.json
if [ -f "$TMP/settings.json" ]; then
    python3 - "$TMP/settings.json" <<'PY'
import json, sys
alt = json.load(open(sys.argv[1], encoding='utf-8-sig')); neu = json.load(open('settings.json', encoding='utf-8-sig'))
if isinstance(alt.get('echtgeld'), dict):
    neu['echtgeld'] = alt['echtgeld']
    json.dump(neu, open('settings.json', 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
    print('Modus beibehalten:', 'Echtgeld' if alt['echtgeld'].get('aktiv') else 'Papier')
PY
fi
rm -rf "$TMP"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install -q .
find . -type d -name "__pycache__" -prune -exec rm -rf {} +
chmod +x *.sh
sh ./install_service.sh
echo 'Update fertig. Naechster Lauf beim naechsten 5-Minuten-Takt.'

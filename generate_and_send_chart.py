# generate_and_send_chart.py (Version 3 - mit einzigartigen Dateinamen gegen Caching)
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import json
import sys
import os
import subprocess
import time # Import für Zeitstempel

def send_photo_to_telegram_with_curl(bot_token, chat_id, photo_path, caption=""):
    """Sendet ein Bild an einen Telegram-Chat mithilfe des robusten curl-Befehls."""
    try:
        command = [
            'curl', '-s', '-X', 'POST',
            f'https://api.telegram.org/bot{bot_token}/sendPhoto',
            '-F', f'chat_id={chat_id}',
            '-F', f'photo=@{photo_path}',
            '-F', f'caption={caption}'
        ]
        result = subprocess.run(command, capture_output=True, text=True)

        if result.returncode != 0:
            print(f"Fehler beim Ausführen des curl-Befehls: {result.stderr}")
            return False

        response_json = json.loads(result.stdout)
        if not response_json.get('ok'):
            print(f"Fehler von Telegram API erhalten: {response_json.get('description')}")
            return False

        return True

    except FileNotFoundError:
        print("Fehler: Der Befehl 'curl' wurde nicht gefunden. Bitte sicherstellen, dass curl installiert ist.")
        return False
    except Exception as e:
        print(f"Ein unerwarteter Fehler ist beim Senden via curl aufgetreten: {e}")
        return False

def main():
    if len(sys.argv) < 2:
        print("Fehler: Bitte gib den Namen der CSV-Datei an.")
        return

    csv_filename = sys.argv[1]

    try:
        print(f"Lese Daten aus '{csv_filename}'...")
        df = pd.read_csv(csv_filename)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
    except FileNotFoundError:
        print(f"Fehler: Die Datei '{csv_filename}' wurde nicht gefunden.")
        return

    print("Erstelle Diagramme...")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), sharex=True, facecolor='white')

    start_date = df['timestamp'].min().strftime('%Y-%m-%d')
    end_date = df['timestamp'].max().strftime('%Y-%m-%d')
    fig.suptitle(f'Analyse der Portfolio-Performance ({start_date} bis {end_date})', fontsize=16)

    ax1.plot(df['timestamp'], df['equity'], color='#007ACC', label='Kontostand', linewidth=2)
    ax1.set_title('Equity Curve (Kontostand-Entwicklung)')
    ax1.set_ylabel('Kontostand (USDT)')
    ax1.grid(True, linestyle='--', alpha=0.6)
    ax1.legend()
    formatter_equity = mticker.FuncFormatter(lambda x, p: f'{x/1e6:.2f}M' if x >= 1e6 else (f'{x/1e3:.1f}k' if x >= 1e3 else f'{x:.0f}'))
    ax1.yaxis.set_major_formatter(formatter_equity)
    ax1.set_facecolor('#f0f0f0')

    ax2.fill_between(df['timestamp'], -df['drawdown_pct'] * 100, 0, color='#D32F2F', alpha=0.4, label='Drawdown')
    ax2.set_title('Drawdown-Verlauf')
    ax2.set_xlabel('Datum')
    ax2.set_ylabel('Drawdown (%)')
    ax2.grid(True, linestyle='--', alpha=0.6)
    formatter_pct = mticker.FuncFormatter(lambda x, p: f'{-x:.1f}%')
    ax2.yaxis.set_major_formatter(formatter_pct)
    ax2.legend()
    ax2.set_facecolor('#f0f0f0')

    # NEU: Erstelle einen einzigartigen Dateinamen, um Caching zu verhindern
    unique_id = int(time.time())
    chart_filename = f'chart_{unique_id}.png'

    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    plt.savefig(chart_filename)
    plt.close()
    print(f"Diagramm wurde temporär als '{chart_filename}' gespeichert.")

    try:
        with open('secret.json', 'r') as f:
            secrets = json.load(f)
        telegram_config = secrets.get('telegram', {})
        bot_token = telegram_config.get('bot_token')
        chat_id = telegram_config.get('chat_id')

        if bot_token and chat_id:
            print("Sende Diagramm an Telegram...")
            caption = f"Grafischer Backtest-Bericht für '{csv_filename}'."
            if send_photo_to_telegram_with_curl(bot_token, chat_id, chart_filename, caption):
                print("✔ Diagramm erfolgreich an Telegram gesendet!")
            else:
                print("❌ Senden fehlgeschlagen.")
        else:
            print("Fehler: Telegram bot_token oder chat_id in secret.json nicht gefunden.")
    except Exception as e:
        print(f"Ein Fehler ist aufgetreten: {e}")

    finally:
        if os.path.exists(chart_filename):
            os.remove(chart_filename)
            print(f"Temporäre Datei '{chart_filename}' wurde gelöscht.")

if __name__ == "__main__":
    main()

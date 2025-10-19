# tests/test_structure.py
import os
import sys
import pytest

# Füge das Projektverzeichnis zum Python-Pfad hinzu, damit Imports funktionieren
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(PROJECT_ROOT, 'src'))

def test_project_structure():
    """Stellt sicher, dass alle erwarteten Hauptverzeichnisse existieren."""
    assert os.path.isdir(os.path.join(PROJECT_ROOT, 'src')), "Das 'src'-Verzeichnis fehlt."
    assert os.path.isdir(os.path.join(PROJECT_ROOT, 'artifacts')), "Das 'artifacts'-Verzeichnis fehlt."
    assert os.path.isdir(os.path.join(PROJECT_ROOT, 'tests')), "Das 'tests'-Verzeichnis fehlt."

def test_core_script_imports():
    """
    Stellt sicher, dass die wichtigsten Funktionen aus den Kernmodulen importiert werden können.
    Dies ist ein schneller Check, ob die grundlegende Code-Struktur intakt ist.
    """
    try:
        # Importiere nur die Funktionen, die in der aktuellen Version existieren
        from jaegerbot.utils.trade_manager import housekeeper_routine, check_and_open_new_position, full_trade_cycle
        from jaegerbot.utils.exchange import Exchange
        from jaegerbot.utils.ann_model import load_model_and_scaler, create_ann_features
        from jaegerbot.analysis.backtester import run_ann_backtest
        # KORREKTUR: Wir importieren jetzt 'main' aus dem optimizer und geben ihr einen Alias
        from jaegerbot.analysis.optimizer import main as optimizer_main
        from jaegerbot.analysis.portfolio_optimizer import run_portfolio_optimizer
    except ImportError as e:
        pytest.fail(f"Kritischer Import-Fehler. Die Code-Struktur scheint defekt zu sein. Fehler: {e}")

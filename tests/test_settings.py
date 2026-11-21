import json, tempfile, unittest
from pathlib import Path
from titanbot import settings as ST


class SettingsTest(unittest.TestCase):
    def test_defaults_when_missing_or_broken(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(ST.load(Path(d) / 'fehlt.json'), ST.DEFAULTS)
            p = Path(d) / 's.json'; p.write_text('{kaputt', encoding='utf-8')
            self.assertEqual(ST.load(p), ST.DEFAULTS)
            self.assertFalse(ST.DEFAULTS['echtgeld'])                  # ohne settings.json nie Echtgeld
            self.assertFalse(ST.DEFAULTS['titanbot'])

    def test_repo_settings(self):
        s = ST.load(ST.ROOT / 'settings.json')
        self.assertEqual(s, dict(titanbot=True, einsatz_stark=0.0625, einsatz_normal=0.0375, hebel=1, max_offen=20,
                                 echtgeld=False, papier_start=200.0))

    def test_invalid_values_fall_back(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 's.json'
            p.write_text(json.dumps({'titanbot': {'aktiv': True, 'einsatz_oi_stark': 3, 'hebel': 50, 'max_offen': 0},
                                     'echtgeld': {'aktiv': 'ja'}}), encoding='utf-8')
            s = ST.load(p)
            self.assertEqual((s['einsatz_stark'], s['hebel'], s['max_offen'], s['echtgeld']), (0.0625, 1, 20, False))


if __name__ == '__main__':
    unittest.main()

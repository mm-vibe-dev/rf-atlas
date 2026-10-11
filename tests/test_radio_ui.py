"""Frontend contract checks for the local receiver interface."""
from html.parser import HTMLParser
from pathlib import Path
import unittest

import rfatlas
ASSETS = Path(rfatlas.__file__).resolve().parent / 'web_assets'

class Elements(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids = []
        self.nav = []
        self.feed(text)
    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if 'id' in values:
            self.ids.append(values['id'])
        if 'data-nav' in values:
            self.nav.append(values['data-nav'])

class RadioUITests(unittest.TestCase):
    def test_navigation_preserves_explorer_and_catalog(self):
        page = Elements((ASSETS / 'index.html').read_text(encoding='utf-8'))
        self.assertEqual(len(page.ids), len(set(page.ids)))
        self.assertEqual(page.nav, ['live', 'signals', 'settings', 'devices', 'stations', 'decoder', 'timeline', 'tuner'])
        for name in ('explorerform', 'powerplot', 'waterfall', 'signals', 'investigation',
                     'livespectrum', 'livewaterfall', 'tunerform', 'settingsform', 'frameview'):
            self.assertIn(name, page.ids)

    def test_local_assets_and_explicit_retention(self):
        text = (ASSETS / 'index.html').read_text(encoding='utf-8')
        self.assertIn('/assets/radio.js', text)
        self.assertIn('/assets/radio.css', text)
        self.assertIn('may contain identifiers and message content', text)
        for name in ('retain_frames', 'retain_identifiers', 'startup_auto'):
            self.assertNotIn(f'name="{name}" type="checkbox" checked', text)
        self.assertNotIn('https://', text)

if __name__ == '__main__':
    unittest.main()

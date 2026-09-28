"""Localization and distributable-source checks; no account or network needed."""
import ast
import importlib.util
import json
from pathlib import Path
import unittest

from tools.localize import ROOT, CJK, translate_source


class LocalizationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads((ROOT/'translations/en.json').read_text(encoding='utf-8'))

    def test_every_english_source_compiles_and_has_no_chinese_strings(self):
        for name in ['app.py', 'client.py', 'remote_engine.py', 'build_info.py', 'dialogs.py', 'updater.py']:
            if not (ROOT/name).exists():
                continue
            source = translate_source((ROOT/name).read_text(encoding='utf-8'), self.catalog)
            compile(source, name, 'exec')
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertIsNone(CJK.search(node.value), (name, node.value))

    def test_unknown_translation_fails_instead_of_shipping_mixed_language(self):
        with self.assertRaises(ValueError):
            translate_source("label = '没有翻译的文字'", self.catalog)

    def test_translation_preserves_code_and_protocol_keys(self):
        class RemoveDisplayText(ast.NodeTransformer):
            def visit_Constant(inner_self, node):
                if isinstance(node.value, str) and (node.value in self.catalog or node.value in self.catalog.values()):
                    return ast.copy_location(ast.Constant(value='DISPLAY_TEXT'), node)
                return node
        # The engine contains all destructive operations; localization changes only display constants.
        source = (ROOT/'remote_engine.py').read_text(encoding='utf-8')
        original = RemoveDisplayText().visit(ast.parse(source))
        translated = RemoveDisplayText().visit(ast.parse(translate_source(source, self.catalog)))
        self.assertEqual(ast.dump(original), ast.dump(translated))

    def test_english_report_and_login_error(self):
        source = translate_source((ROOT/'client.py').read_text(encoding='utf-8'), self.catalog)
        scope = {'__file__': str(ROOT/'client.py'), '__name__': 'english_client_test'}
        exec(compile(source, 'english_client', 'exec'), scope)
        report = {'time': 0, 'host': 'test-host', 'username': 'z1234567', 'quota': {'used': 1024, 'soft': 2048}}
        text = scope['report_text'](report)
        self.assertIn('Account: z1234567', text)
        self.assertIn('No eligible cleanup items.', text)
        self.assertIsNone(CJK.search(text))
        with self.assertRaisesRegex(ValueError, 'Enter a valid zID'):
            scope['Session']().connect('invalid', '')


if __name__ == '__main__':
    unittest.main()

"""Build the English edition without changing application behavior.

Only string constants change, including literal portions of f-strings. The
catalog must cover every Chinese string; a missing translation fails the build.
Both editions use the same reviewed cleanup logic and JSON protocol keys.
"""
import ast
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CJK = re.compile(r'[\u3400-\u9fff]')
PUNCTUATION = str.maketrans({'：': ': ', '。': '.', '（': '(', '）': ')', '，': ', ', '；': '; '})


def translate_source(source, catalog):
    class Translate(ast.NodeTransformer):
        def visit_Constant(self, node):
            if isinstance(node.value, str):
                value = node.value
                if CJK.search(value):
                    if value not in catalog or not catalog[value] or CJK.search(catalog[value]):
                        raise ValueError('Missing English translation: '+repr(value))
                    value = catalog[value]
                else:
                    value = value.translate(PUNCTUATION)
                return ast.copy_location(ast.Constant(value=value), node)
            return node
    return ast.unparse(ast.fix_missing_locations(Translate().visit(ast.parse(source))))+'\n'


def prepare(language, destination):
    if language not in {'zh', 'en'}:
        raise ValueError('Unsupported language')
    destination = Path(destination).resolve()
    build_root = (ROOT/'build').resolve()
    if not destination.is_relative_to(build_root) or destination == build_root:
        raise ValueError('Generated sources must stay inside a build subdirectory')
    destination.mkdir(parents=True, exist_ok=True)
    catalog = json.loads((ROOT/'translations/en.json').read_text(encoding='utf-8'))
    names = ['app.py', 'client.py', 'remote_engine.py', 'build_info.py', 'dialogs.py']
    if (ROOT/'updater.py').exists():
        names.append('updater.py')
    for name in names:
        source = (ROOT/name).read_text(encoding='utf-8-sig')
        if name == 'build_info.py':
            source = source.replace("LANGUAGE = 'zh'", 'LANGUAGE = '+repr(language))
        if language == 'en':
            source = translate_source(source, catalog)
        (destination/name).write_text(source, encoding='utf-8')
    (destination/'app.ico').write_bytes((ROOT/'app.ico').read_bytes())
    return destination


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('language', choices=['zh', 'en'])
    args = parser.parse_args()
    print(prepare(args.language, ROOT/'build'/('source-'+args.language)))

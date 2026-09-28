import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from remote_engine import Engine, SafetyError, parse_quota

A, B, C = 'a'*40, 'b'*40, 'c'*40


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cse-helper-test-')
        self.home = Path(self.temp.name)
        self.root = self.home/'.vscode-server'
        (self.root/'cli/servers').mkdir(parents=True)
        (self.root/'extensions').mkdir()
        self.write('extensions/extensions.json', [])
        self.write('extensions/.obsolete', {})
        self.write('cli/servers/lru.json', [])
        self.processes = {'paths': [], 'count': 0, 'uncertain': False}
        self.engine = Engine(self.home, B, processes=lambda: self.processes,
                             quota=lambda: {'used': 100000, 'soft': 200000, 'hard': 300000, 'over': False})
        self.server(A, '1.100.0')
        self.server(B, '1.101.0')
        (self.home/'COMP2521').mkdir()
        (self.home/'COMP2521/assignment.c').write_text('COURSE WORK')

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative, content):
        path = self.root/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content), encoding='utf-8')
        return path

    def aged(self, path):
        old = time.time()-15*86400
        for p in list(path.rglob('*'))+[path] if path.is_dir() else [path]:
            if not p.is_symlink():
                os.utime(p, (old, old))
        return path

    def server(self, commit, version):
        base = self.root/('cli/servers/Stable-'+commit)/'server'
        self.write(str(base.relative_to(self.root)/'product.json'), {'commit': commit, 'version': version})
        (base/'bin').mkdir()
        (base/'bin/code-server').write_text('test')
        (base/'node').write_text('test')
        self.aged(base.parent)
        return base.parent

    def cache(self, name='cache.vsix', content='x'*4096):
        path = self.root/'data/CachedExtensionVSIXs'/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return self.aged(path)

    def extension(self, name, selected=False, obsolete=True):
        path = self.write('extensions/'+name+'/package.json', {'version': '1.0.0'})
        self.aged(path.parent)
        if selected:
            self.write('extensions/extensions.json', [{'relativeLocation': name}])
        if obsolete:
            self.write('extensions/.obsolete', {name: True})
        return path.parent

    def candidates(self):
        return self.engine.scan(usage=False)['candidates']

    def selection(self, rows):
        return [{'path': row['path'], 'stamp': row['stamp']} for row in rows]

    def test_only_old_server_is_candidate(self):
        names = [r['path'] for r in self.candidates()]
        self.assertIn('.vscode-server/cli/servers/Stable-'+A, names)
        self.assertNotIn('.vscode-server/cli/servers/Stable-'+B, names)

    def test_current_older_client_and_newest_both_preserved(self):
        self.engine.local_commit = A
        self.assertFalse([r for r in self.candidates() if r['kind']=='server'])

    def test_latest_by_version_not_mtime(self):
        self.engine.local_commit = ''
        self.server(C, '1.99.0')
        self.assertEqual(len([r for r in self.candidates() if r['kind']=='server']), 2)
        self.assertNotIn('.vscode-server/cli/servers/Stable-'+B, [r['path'] for r in self.candidates()])

    def test_active_server_is_preserved(self):
        self.processes.update(paths=[str(self.root/('cli/servers/Stable-'+A)/'server/node')], count=1)
        self.assertFalse([r for r in self.candidates() if r['kind']=='server'])

    def test_unknown_process_visibility_disables_cleanup(self):
        self.cache()
        self.processes['uncertain'] = True
        self.assertEqual(self.candidates(), [])

    def test_selected_obsolete_extension_is_preserved(self):
        self.extension('test.ext-1.0.0', selected=True)
        self.assertFalse([r for r in self.candidates() if r['kind']=='extension'])

    def test_profile_selected_extension_is_preserved(self):
        self.extension('test.ext-1.0.0')
        self.write('data/User/profiles/other/extensions.json', [{'relativeLocation': 'test.ext-1.0.0'}])
        self.assertFalse([r for r in self.candidates() if r['kind']=='extension'])

    def test_unselected_explicitly_obsolete_extension_is_candidate(self):
        self.extension('test.ext-1.0.0')
        self.assertEqual(len([r for r in self.candidates() if r['kind']=='extension']), 1)

    def test_active_vscode_prevents_extension_deletion(self):
        self.extension('test.ext-1.0.0')
        self.processes['count'] = 1
        self.assertFalse([r for r in self.candidates() if r['kind']=='extension'])

    def test_broken_manifest_preserves_all_extensions(self):
        self.extension('test.ext-1.0.0')
        (self.root/'extensions/extensions.json').write_text('not json')
        self.assertFalse([r for r in self.candidates() if r['kind']=='extension'])

    def test_forged_outside_path_cannot_be_cleaned(self):
        path = self.home/'COMP2521/assignment.c'
        report = self.engine.clean([{'path':'COMP2521/assignment.c', 'stamp':'anything'},
                                    {'path':'../outside', 'stamp':'anything'},
                                    {'path': str(path), 'stamp':'anything'}])
        self.assertEqual(path.read_text(), 'COURSE WORK')
        self.assertEqual(len(report['cleanup']['removed']), 0)
        self.assertEqual(len(report['cleanup']['skipped']), 3)

    def test_modified_after_scan_is_preserved(self):
        path = self.cache()
        rows = [r for r in self.candidates() if r['kind']=='cache']
        path.write_text('changed after scanning')
        report = self.engine.clean(self.selection(rows))
        self.assertTrue(path.exists())
        self.assertEqual(len(report['cleanup']['skipped']), 1)

    def test_profile_change_after_scan_is_respected(self):
        path = self.extension('test.ext-1.0.0')
        rows = [r for r in self.candidates() if r['kind']=='extension']
        self.write('extensions/extensions.json', [{'relativeLocation': path.name}])
        report = self.engine.clean(self.selection(rows))
        self.assertTrue(path.exists())
        self.assertEqual(len(report['cleanup']['removed']), 0)

    def test_successful_cleanup_idempotent_and_course_preserved(self):
        cache = self.cache()
        self.extension('test.ext-1.0.0')
        history = self.write('data/User/History/keep.json', {'work':'unsaved history'})
        rows = self.candidates()
        result = self.engine.clean(self.selection(rows))
        self.assertEqual(len(result['cleanup']['removed']), 3)
        self.assertTrue(result['cleanup']['write_ok'])
        self.assertFalse(cache.exists())
        self.assertTrue((self.root/('cli/servers/Stable-'+B)).exists())
        self.assertTrue(history.exists())
        self.assertEqual((self.home/'COMP2521/assignment.c').read_text(), 'COURSE WORK')
        second = self.engine.clean(self.selection(rows))
        self.assertEqual(second['cleanup']['removed'], [])
        self.assertEqual(json.loads((self.root/'extensions/.obsolete').read_text()), {})

    def test_fresh_descendant_in_old_cache_folder_preserved(self):
        folder = self.root/'data/CachedExtensionVSIXs/old-folder'
        folder.mkdir(parents=True)
        (folder/'file').write_text('fresh')
        os.utime(folder, (time.time()-999999, time.time()-999999))
        self.assertFalse([r for r in self.candidates() if r['kind']=='cache'])

    def test_busy_nfs_file_preserves_entire_directory(self):
        path = self.root/('cli/servers/Stable-'+A)
        (path/'.nfs123').write_text('in use')
        self.aged(path)
        self.assertFalse([r for r in self.candidates() if r['kind']=='server'])

    def test_partial_server_preserved(self):
        (self.root/('cli/servers/Stable-'+A)/'server/node').unlink()
        self.assertFalse([r for r in self.candidates() if r['kind']=='server'])

    def symlink(self, source, target, directory=False):
        try:
            source.symlink_to(target, target_is_directory=directory)
        except OSError:
            self.skipTest('This Windows account cannot create symlinks; tested on Linux too.')

    def test_external_cache_symlink_is_not_followed(self):
        self.cache().unlink()
        external = self.home/'COMP2521'
        link = self.root/'data/CachedExtensionVSIXs/link'
        self.symlink(link, external, True)
        self.assertFalse([r for r in self.candidates() if r['kind']=='cache'])
        self.assertTrue((external/'assignment.c').exists())

    def test_parent_symlink_cannot_escape(self):
        import shutil
        extensions = self.root/'extensions'
        shutil.rmtree(extensions)
        self.symlink(extensions, self.home/'COMP2521', True)
        self.assertFalse([r for r in self.candidates() if r['kind']=='extension'])
        self.assertTrue((self.home/'COMP2521/assignment.c').exists())

    def test_lock_in_use_blocks_server_removal(self):
        try:
            import fcntl
        except ImportError:
            self.skipTest('Linux flock test runs on remote Linux.')
        folder = self.root/'cli/servers/.locks'
        folder.mkdir()
        rows = [r for r in self.candidates() if r['kind']=='server']
        with (folder/('Stable-'+A)).open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
            report = self.engine.clean(self.selection(rows))
        self.assertEqual(len(report['cleanup']['removed']), 0)
        self.assertTrue((self.root/('cli/servers/Stable-'+A)).exists())

    def test_quota_units_and_unknown(self):
        quota = parse_quota('Disk quotas:\n /import/glass/6 2719M* 2304M 2802M 6days 12982 236k')
        self.assertTrue(quota['over'])
        self.assertEqual(quota['soft'], 2304*1024**2)
        self.assertIsNone(parse_quota('not available')['used'])


if __name__ == '__main__':
    unittest.main(verbosity=2)

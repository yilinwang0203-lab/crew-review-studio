import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import serve


class ProjectRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.old = serve.REGISTRY
        serve.REGISTRY = self.dir / 'projects.json'
        self.addCleanup(setattr, serve, 'REGISTRY', self.old)

    def test_registry_plus_cli_dedup_and_names(self):
        a, b = self.dir / 'a', self.dir / 'x' / 'a'
        a.mkdir(); b.mkdir(parents=True)
        serve.REGISTRY.write_text(json.dumps([{'name': 'A', 'path': str(a)}, {'path': str(b)}]))
        out = serve.load_projects([str(a), str(b)])
        self.assertEqual([('A', a.resolve()), ('a', b.resolve())], [(p['name'], p['path']) for p in out])

    def test_duplicate_names_get_suffix(self):
        a, b = self.dir / 'p1' / 'same', self.dir / 'p2' / 'same'
        a.mkdir(parents=True); b.mkdir(parents=True)
        out = serve.load_projects([str(a), str(b)])
        self.assertEqual(['same', 'same-2'], [p['name'] for p in out])

    def test_empty_registry_falls_back_to_default(self):
        self.assertEqual(serve.DEFAULT_PROJECT.resolve(), serve.load_projects([])[0]['path'])

    def test_broken_registry_is_ignored(self):
        serve.REGISTRY.write_text('{broken')
        self.assertEqual(1, len(serve.load_projects([])))

    def test_relative_registry_path_is_relative_to_registry(self):
        serve.REGISTRY.write_text(json.dumps([{'name': 'demo', 'path': '../demo'}]))
        self.assertEqual((self.dir.parent / 'demo').resolve(), serve.load_projects()[0]['path'])


if __name__ == '__main__':
    unittest.main()

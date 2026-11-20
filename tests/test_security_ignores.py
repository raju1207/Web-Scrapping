import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which('git'),'Git is required for ignore-rule inspection')
class SecurityIgnoreTests(unittest.TestCase):
    def test_secret_artifacts_are_ignored_without_hiding_implementation(self):
        root=Path(__file__).resolve().parents[1]
        protected=['.env.production','.env.local','private.key','private.pem','identity.p12',
            'pgpass.conf','private/auth_headers.json','browser/storage_state.json',
            'config/udise_headers.json','cookies.json','browser/browser_state.json',
            'credentials/export.json','export.dump','snapshot.backup','backups/export.sql',
            'backup_exports/files.zip','archive/cleanup_backups/retained.json']
        public=['.env.example','.env.template','school_scraping/production_safety.py',
            'database/migrations/001_scraping_memory.sql','tests/test_production_safety.py',
            'docs/PRODUCTION_READINESS.md','config/production_safety.example.json','tests/fixtures/headers.example.json']
        result=subprocess.run(['git','--no-optional-locks','check-ignore','--no-index','--stdin','-z'],
            input=('\0'.join(protected+public)+'\0').encode(),cwd=root,capture_output=True)
        self.assertIn(result.returncode,(0,1))
        ignored=set(result.stdout.decode().split('\0'))
        for name in protected:
            with self.subTest(path=name):self.assertIn(name,ignored)
        for name in public:
            with self.subTest(path=name):self.assertNotIn(name,ignored)

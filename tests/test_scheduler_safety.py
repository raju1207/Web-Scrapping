"""Admission integration never opens a source, database or historical queue."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import daily_scheduler as scheduler
from school_scraping.scheduler_safety import evaluate
from school_scraping.supervision import SupervisionResult


class SchedulerAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        config = json.loads((scheduler.PROJECT_DIR/'config/states_pincodes.json').read_text())
        config['scheduler']['enabled'] = True
        self.config = self.root/'test-config.json'
        self.config.write_text(json.dumps(config))
        self.profile = self.root/'profile.json'
        self.policy = {'schema_version':1,'approved':True,'approval_reference':'UNIT-TEST-ONLY',
            'scheduler':{'approved':True,'hard_timeout_seconds':2},
            'database':{'host':'127.0.0.1','port':55449,'name':'section13b_test_unit',
                'tls':{'mode':'approved-local','approved_local_exception':True,'reason':'Isolated unit fixture'},
                'timeouts':{'connect_seconds':1,'statement_ms':1000,'lock_ms':500}}}
        self.profile.write_text(json.dumps(self.policy))

    def args(self, *extra):
        argv=['--config',str(self.config),'--source','saras','--state','CHANDIGARH',
            '--storage-mode','database','--new-only','--database','--limit','1',
            '--data-dir',str(self.root/'absent-data'),*extra]
        args=scheduler.build_parser().parse_args(argv)
        scheduler.prepare_args(args,argv)
        return args

    def test_missing_profile_blocks_before_legacy_journal_or_process(self):
        with patch.object(scheduler,'_memory') as memory, patch.object(scheduler,'supervise') as supervise, \
                patch.object(scheduler.subprocess,'Popen') as popen:
            events=[]
            results=scheduler.run_daily_job(self.args(),threading.Event(),lambda event,**fields:events.append(event))
        self.assertTrue(all(row['status']=='failed' for row in results))
        self.assertIn('production_preflight_blocked',events)
        memory.assert_not_called();supervise.assert_not_called();popen.assert_not_called()
        self.assertFalse((self.root/'absent-data').exists())

    def test_approved_profile_binds_timeout_and_nonsecret_runtime_policy(self):
        args=self.args('--production-profile',str(self.profile))
        with patch.dict(os.environ,{'DB_PASSWORD':'private-fixture-value'}):
            report,env=evaluate(args,preflight=Mock(return_value={'status':'PASS','errors':[]}))
        self.assertEqual(report['status'],'PASS')
        self.assertEqual(args.child_timeout,2)
        self.assertEqual(env['DB_STATEMENT_TIMEOUT_MS'],'1000')
        self.assertEqual(env['PYTHON_DOTENV_DISABLED'],'1')
        self.assertNotIn('private-fixture-value',json.dumps(report))

    def test_larger_timeout_cannot_override_approved_budget(self):
        checker=Mock()
        report,env=evaluate(self.args('--production-profile',str(self.profile),'--child-timeout','3'),preflight=checker)
        self.assertEqual(report['status'],'BLOCKED');self.assertIsNone(env);checker.assert_not_called()

    def test_every_job_is_checked_before_any_launch(self):
        args=self.args('--production-profile',str(self.profile))
        args.resolved_jobs.append(('udise',args.resolved_jobs[0][1]))
        checker=Mock(side_effect=[{'status':'PASS'},{'status':'BLOCKED','errors':['YEAR_NOT_APPROVED']}])
        report,env=evaluate(args,preflight=checker)
        self.assertEqual(checker.call_count,2)
        self.assertEqual(report['status'],'BLOCKED');self.assertIsNone(env)

    def test_profile_revocation_is_reloaded_for_next_cycle(self):
        args=self.args('--production-profile',str(self.profile))
        report,_=evaluate(args,preflight=Mock(return_value={'status':'PASS'}))
        self.assertEqual(report['status'],'PASS')
        self.policy['approved']=False;self.profile.write_text(json.dumps(self.policy))
        report,env=evaluate(args)
        self.assertEqual(report['status'],'BLOCKED');self.assertIsNone(env)

    def test_errors_do_not_print_exception_secrets(self):
        report,env=evaluate(self.args('--production-profile',str(self.profile)),
            preflight=Mock(side_effect=RuntimeError('password=private-marker')))
        self.assertEqual(report['status'],'BLOCKED');self.assertIsNone(env)
        self.assertNotIn('private-marker',json.dumps(report))

    def test_malformed_profile_scheduler_is_safely_blocked(self):
        self.policy['scheduler']=['invalid'];self.profile.write_text(json.dumps(self.policy))
        report,env=evaluate(self.args('--production-profile',str(self.profile)))
        self.assertEqual(report['status'],'BLOCKED');self.assertIsNone(env)

    def test_unapproved_example_preflight_only_never_connects_or_launches(self):
        output=io.StringIO()
        with patch('psycopg.connect') as connect,patch.object(scheduler,'supervise') as supervise, \
                patch.object(scheduler,'SourceLock') as lock,contextlib.redirect_stdout(output):
            code=scheduler.main(['--source','saras','--state','CHANDIGARH','--database','--storage-mode','database',
                '--new-only','--child-timeout','2','--production-profile',
                str(scheduler.PROJECT_DIR/'config/production_safety.example.json'),'--preflight-only'])
        self.assertEqual(code,2)
        self.assertEqual(json.loads(output.getvalue())['children_launched'],0)
        connect.assert_not_called();supervise.assert_not_called();lock.assert_not_called()

    def test_dry_run_stays_offline_even_with_profile(self):
        output=io.StringIO()
        with patch('psycopg.connect') as connect,patch.object(scheduler,'supervise') as supervise,contextlib.redirect_stdout(output):
            code=scheduler.main(['--source','saras','--state','CHANDIGARH','--database','--storage-mode','database',
                '--new-only','--production-profile',str(self.profile),'--dry-run'])
        self.assertEqual(code,0)
        self.assertEqual(json.loads(output.getvalue())['production_preflight'],'NOT_RUN_OFFLINE_DRY_RUN')
        connect.assert_not_called();supervise.assert_not_called()

    def test_real_admission_failure_cannot_start_enabled_once_scheduler(self):
        output=io.StringIO()
        with patch.object(scheduler,'supervise') as supervise,patch.object(scheduler,'SourceLock') as lock,contextlib.redirect_stdout(output):
            code=scheduler.main(['--config',str(self.config),'--source','saras','--state','CHANDIGARH',
                '--database','--storage-mode','database','--new-only','--once','--data-dir',str(self.root/'absent-data')])
        self.assertEqual(code,2);supervise.assert_not_called();lock.assert_not_called()
        self.assertFalse((self.root/'absent-data').exists())

    def test_admitted_job_passes_private_environment_only_to_supervisor(self):
        args=self.args('--production-profile',str(self.profile));args.child_timeout=2
        events=[];private={'DB_PASSWORD':'private-marker','PYTHON_DOTENV_DISABLED':'1'}
        with patch('school_scraping.scheduler_safety.evaluate',return_value=({'status':'PASS'},private)), \
                patch.object(scheduler,'supervise',return_value=SupervisionResult(0,False,False,0.1,True,False)) as supervise:
            results=scheduler.run_daily_job(args,threading.Event(),lambda event,**body:events.append(body))
        self.assertEqual(results[0]['status'],'success')
        self.assertIs(supervise.call_args.kwargs['env'],private)
        self.assertNotIn('private-marker',json.dumps(events))

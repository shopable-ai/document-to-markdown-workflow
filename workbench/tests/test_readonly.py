"""Isolated evidence fixtures, never point at production state or run roots."""
import hashlib
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


def dump(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')


def ts(delta=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=delta)).isoformat()


class Fixture:
    def __init__(self, root):
        self.root = root
        self.state_root = root / 'state'
        self.output = root / 'output'
        self.batch = self.output / 'conversions/hybrid'
        self.run_id = 'collection-fixture'
        self.task = self.state_root / self.run_id
        self.sources = ['/input/a/同名.pdf', '/input/b/同名.pdf', '/input/c/待处理.pdf']
        self.generation = ts(-10)
        self.state = {'run_id': self.run_id, 'task_type': 'pdf-collection', 'status': 'running', 'route': 'hybrid', 'run_root': str(self.batch), 'expected_sources': 3, 'started_at': ts(-100), 'updated_at': ts(-99), 'machine_resource_policy': {'local_pdf_capacity': 1, 'visual_model_capacity': 1}}
        self.reg = {s: {'path': str(self.batch / f'book-{i}'), 'source_sha256': f'{i:064x}'} for i, s in enumerate(self.sources, 1)}
        self.items = [{**{'source_path': s, 'run_root': self.reg[s]['path'], 'source_sha256': self.reg[s]['source_sha256'], 'route': 'hybrid', 'started_at': ts(-40)}, **fields} for s, fields in zip(self.sources, [{'status': 'passed', 'accepted': True, 'full_pdf_coverage': True, 'total_pages': 20, 'finished_at': ts(-20)}, {'status': 'running'}])]
        self.publish()
        self.task.mkdir(parents=True, exist_ok=True)
        (self.task / 'source-list.txt').write_text('\n'.join(self.sources)+'\n', encoding='utf-8')
        dump(self.task / 'progress-current.json', self.state)
        package = Path(self.reg[self.sources[1]]['path'])
        dump(package / 'progress.json', {'source': self.sources[1], 'source_sha256': self.reg[self.sources[1]]['source_sha256'], 'updated_at': ts(-2), 'current_stage': 'page-processing', 'completed_pages': [1,2,3], 'total_pages': 10})
        dump(self.output / 'conversions/_consolidated/source-consolidation.json', {'unverified_schema': True})

    def publish(self):
        counts = {k: sum(r['status']==status for r in self.items) for k,status in [('passed','passed'),('failed','failed'),('needs_review','needs-review'),('running','running')]}
        terminal = counts['passed']+counts['failed']+counts['needs_review']
        summary = {**counts, 'planned_total': len(self.sources), 'completed_total': terminal, 'pending_total': len(self.sources)-terminal}
        strict = sum(r['status']=='passed' and r.get('accepted') is True and r.get('full_pdf_coverage') is True for r in self.items)
        common = {'batch_id': 'fixture', 'updated_at': self.generation}
        dump(self.batch/'manifest.json', {**common, 'inputs': [{'source_path': s} for s in self.sources], 'run_roots': self.reg})
        dump(self.batch/'results.json', {**common, 'items': self.items})
        dump(self.batch/'progress-current.json', {'updated_at': self.generation, 'summary': summary, 'active_source': next((r['source_path'] for r in self.items if r['status']=='running'),None), 'strict_accepted_count': strict})

    def observe(self):
        return server.project(self.state_root, self.output, self.run_id)

    def hashes(self):
        return {str(p.relative_to(self.root)): (hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_mtime_ns) for p in self.root.rglob('*') if p.is_file()}


class ReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.f = Fixture(Path(self.temp.name).resolve())

    def tearDown(self):
        self.temp.cleanup()

    def test_same_names_progress_strict_and_pending(self):
        with patch('server.worker_verified', return_value=True):
            s = self.f.observe()
        self.assertEqual(s['strict_accepted_count'], 1)
        self.assertEqual([r['status'] for r in s['rows']], ['passed','running','pending'])
        self.assertNotEqual(s['rows'][0]['source'],s['rows'][1]['source'])
        self.assertEqual((s['rows'][1]['done'],s['rows'][1]['total']),(3,10))
        self.assertIsNone(s['rows'][1]['strict_in_batch'])

    def test_unverified_worker_is_not_failed_or_running(self):
        before = self.f.hashes()
        s = self.f.observe()
        self.assertEqual(s['status'], 'Unknown')
        self.assertEqual(s['recorded_status'], 'running')
        self.assertEqual(s['rows'][1]['status'], 'Unknown')
        self.assertEqual(self.f.hashes(), before)

    def test_mixed_generation(self):
        path = self.f.batch/'results.json'
        doc = json.loads(path.read_text()); doc['updated_at']=ts(); dump(path,doc)
        s = self.f.observe()
        self.assertIsNone(s['strict_accepted_count']); self.assertEqual(s['reason'],'stale-or-mixed-generation')

    def test_resume_does_not_show_old_snapshot(self):
        self.f.state['resumed_at']=ts(); dump(self.f.task/'progress-current.json',self.f.state)
        self.assertEqual(self.f.observe()['reason'],'stale-or-mixed-generation')

    def test_partial_pass_not_strict(self):
        self.f.items[0]['full_pdf_coverage']=False; self.f.publish()
        s=self.f.observe(); self.assertEqual(s['strict_accepted_count'],0); self.assertFalse(s['rows'][0]['strict_in_batch'])
        self.assertIsNone(s['rows'][0]['done'])

    def test_review_and_failure(self):
        self.f.items[0]['status']='needs-review'; self.f.items[1].update(status='failed',error='fixture error'); self.f.publish()
        s=self.f.observe(); self.assertEqual(s['summary']['needs_review'],1); self.assertEqual(s['rows'][1]['error'],'fixture error')
        self.assertEqual(s['strict_accepted_count'],0)

    def test_unknown_missing_and_bad_json(self):
        p=self.f.batch/'results.json'; p.write_text('{'); self.assertEqual(self.f.observe()['reason'],'batch-evidence-unavailable')
        p.unlink(); self.assertEqual(self.f.observe()['reason'],'batch-evidence-unavailable')
        self.assertEqual(server.project(self.f.state_root,self.f.output,'missing')['status'],'Unknown')

    def test_outside_root_and_symlink(self):
        p=self.f.batch/'results.json'; p.unlink(); p.symlink_to(self.f.task/'progress-current.json')
        self.assertIsNone(server.read(self.f.output,p))
        self.f.state['run_root']=str(self.f.state_root); dump(self.f.task/'progress-current.json',self.f.state)
        self.assertEqual(self.f.observe()['reason'],'batch-root-not-allowed')

    def test_bad_count(self):
        p=self.f.batch/'progress-current.json'; doc=json.loads(p.read_text()); doc['strict_accepted_count']=3; dump(p,doc)
        self.assertEqual(self.f.observe()['reason'],'batch-count-mismatch')

    def test_ledger_is_separate_and_not_invented(self):
        result=server.ledger_info(self.f.output)
        self.assertTrue(result['observed']); self.assertEqual(result['status'],'Unknown')

    def test_actual_process_identity_read_only(self):
        # Disposable test process, NOT a conversion or production worker.
        token='fixture-worker-token'
        with subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)','_worker',token], start_new_session=True) as proc:
            try:
                self.f.state.update(pid=proc.pid,process_group_id=os.getpgid(proc.pid),worker_token=token)
                self.assertTrue(server.worker_verified(self.f.state)); self.f.state['worker_token']='wrong'
                self.assertFalse(server.worker_verified(self.f.state))
            finally:
                proc.terminate(); proc.wait(timeout=5)

    def test_checkpoint_is_not_page_count(self):
        package=Path(self.f.reg[self.f.sources[1]]['path'])
        (package/'progress.json').unlink()
        dump(package/'evidence/page-routes/p007.json',{'page':7,'source_sha256':self.f.reg[self.f.sources[1]]['source_sha256'],'status':'passed'})
        with patch('server.worker_verified',return_value=True): s=self.f.observe()
        self.assertEqual(s['rows'][1]['checkpoint_page'],7);self.assertIsNone(s['rows'][1]['done'])

    def test_http_allowlist_sse_and_read_only(self):
        before=self.f.hashes()
        with server.Server(('127.0.0.1',0),server.Observer(self.f.state_root,self.f.output,[self.f.run_id])) as service:
            thread=threading.Thread(target=service.serve_forever,daemon=True);thread.start()
            def request(path,method='GET',headers=None):
                connection=http.client.HTTPConnection('127.0.0.1',service.server_port,timeout=5)
                connection.request(method,path,headers=headers or {}); response=connection.getresponse()
                return connection,response
            try:
                for path,method,headers,expected in [('/api/snapshot','GET',{},200),('/api/snapshot?path=/etc/passwd','GET',{},404),('/api/snapshot','POST',{},501),('/api/snapshot','GET',{'Host':'evil.example'},403),('/api/snapshot','GET',{'Origin':'https://evil.example'},403)]:
                    c,r=request(path,method,headers); self.assertEqual(r.status,expected);r.read();c.close()
                c,r=request('/api/events');self.assertEqual(r.status,200);first=r.readline().decode();self.assertTrue(first.startswith('id: '));r.readline(); data=json.loads(r.readline().decode()[6:]); self.assertEqual(data['schema'],'d2md-workbench-r3');c.close()
                c,r=request('/api/events',headers={'Last-Event-ID':first.strip()[4:]});self.assertTrue(r.readline().startswith(b'id: '));c.close()
            finally:
                service.shutdown();thread.join(timeout=5)
        self.assertEqual(self.f.hashes(),before)


if __name__ == '__main__':
    unittest.main(verbosity=2)

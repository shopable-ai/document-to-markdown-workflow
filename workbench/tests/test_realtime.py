"""Additional isolated route and live SSE publication regressions."""
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from test_readonly import Fixture, dump, ts, server


class RealtimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.f = Fixture(Path(self.temp.name).resolve())

    def tearDown(self):
        self.temp.cleanup()

    def test_native_checkpoint(self):
        self.f.state['route']='native-only'; dump(self.f.task/'progress-current.json',self.f.state)
        self.f.items[1]['route']='native-only'; self.f.publish()
        package=Path(self.f.reg[self.f.sources[1]]['path']); (package/'progress.json').unlink()
        dump(package/'pages/page-005/summary.json',{'source_sha256':self.f.reg[self.f.sources[1]]['source_sha256'],'status':'passed'})
        with patch('server.worker_verified',return_value=True): result=self.f.observe()
        self.assertEqual(result['rows'][1]['checkpoint_page'],5)
        self.assertEqual(result['rows'][1]['route'],'native-only')
        self.assertIsNone(result['rows'][1]['done'])

    def test_stale_or_wrong_source_checkpoint(self):
        package=Path(self.f.reg[self.f.sources[1]]['path']); path=package/'progress.json'
        doc=json.loads(path.read_text()); doc['updated_at']=ts(-50); dump(path,doc)
        with patch('server.worker_verified',return_value=True): result=self.f.observe()
        self.assertIsNone(result['rows'][1]['phase']); self.assertIsNone(result['rows'][1]['done'])
        doc['updated_at']=ts(); doc['source']=self.f.sources[0]; dump(path,doc)
        with patch('server.worker_verified',return_value=True): result=self.f.observe()
        self.assertIsNone(result['rows'][1]['done'])

    def test_live_sse_full_snapshot_and_reconnect(self):
        f=self.f; token='test-only-observer-worker'
        with subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)','_worker',token],start_new_session=True) as worker:
            try:
                f.state.update(pid=worker.pid,process_group_id=os.getpgid(worker.pid),worker_token=token);dump(f.task/'progress-current.json',f.state)
                with server.Server(('127.0.0.1',0),server.Observer(f.state_root,f.output,[f.run_id])) as service:
                    thread=threading.Thread(target=service.serve_forever,daemon=True);thread.start()
                    def stream(last=None):
                        c=http.client.HTTPConnection('127.0.0.1',service.server_port,timeout=10)
                        c.request('GET','/api/events',headers={'Last-Event-ID':last} if last else {})
                        r=c.getresponse();self.assertEqual(r.status,200);return c,r
                    def event(response):
                        ident=response.readline().decode().strip()[4:]; response.readline(); payload=json.loads(response.readline().decode()[6:]); response.readline();return ident,payload
                    try:
                        c,r=stream(); ident,first=event(r);self.assertTrue(first['batches'][0]['active']);self.assertEqual(first['batches'][0]['rows'][1]['done'],3)
                        path=Path(f.reg[f.sources[1]]['path'])/'progress.json';doc=json.loads(path.read_text());doc.update(completed_pages=list(range(1,11)),current_stage='independent-acceptance',updated_at=ts());dump(path,doc)
                        before=f.hashes(); _,second=event(r);self.assertGreater(second['sequence'],first['sequence']);row=second['batches'][0]['rows'][1];self.assertEqual(row['done'],10);self.assertIsNone(row['strict_in_batch']);self.assertEqual(second['batches'][0]['strict_accepted_count'],1);c.close();self.assertEqual(f.hashes(),before)
                        # A real fixture producer publishes the next generation, not the observer.
                        f.generation=ts();f.items[1].update(status='needs-review',accepted=False,full_pdf_coverage=True,total_pages=10);f.publish()
                        with service.observer.lock: service.observer.last=0
                        before=f.hashes();c,r=stream(ident);_,third=event(r);c.close();self.assertGreater(third['sequence'],second['sequence']);self.assertEqual(third['batches'][0]['rows'][1]['status'],'needs-review');self.assertEqual(third['batches'][0]['strict_accepted_count'],1);self.assertEqual(f.hashes(),before);self.assertIsNone(worker.poll())
                    finally:
                        service.shutdown();thread.join(timeout=5)
            finally:
                worker.terminate();worker.wait(timeout=5)


if __name__=='__main__':
    unittest.main(verbosity=2)

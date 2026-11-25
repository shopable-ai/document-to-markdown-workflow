#!/usr/bin/env python3
"""R3: read registered PDF collection evidence; never invoke lifecycle commands."""
import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

UNKNOWN = 'Unknown'
HERE = Path(__file__).resolve().parent
LIMIT = 8 * 1024 * 1024
RUN_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')
STATES = {'queued', 'running', 'paused', 'cancelled', 'failed', 'completed', 'budget-exhausted', 'needs-review'}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def epoch(value):
    try:
        dt = datetime.fromisoformat(value)
        return dt.timestamp() if dt.tzinfo else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def number(value):
    return value if type(value) is int and value >= 0 else None


def string(value):
    return value if isinstance(value, str) and value else None


def read(root, path, *, raw=False):
    """Fixed local paths only, no symlinks/FIFOs, bounded read; fail closed."""
    try:
        if not path.is_absolute() or not path.is_relative_to(root) or path.resolve() != path:
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as f:
            info = os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
                return None
            data = f.read(LIMIT + 1)
        if len(data) > LIMIT:
            return None
        value = data.decode('utf-8') if raw else json.loads(data)
        return value if raw or isinstance(value, dict) else None
    except (OSError, ValueError, RuntimeError, RecursionError):
        return None


def worker_verified(state):
    # Same identity ingredients as pdf_background_task._task_is_active; no signals or locks.
    pid, pgid, token = number(state.get('pid')), number(state.get('process_group_id')), string(state.get('worker_token'))
    if not pid or not pgid or not token:
        return False
    try:
        out = subprocess.run(['ps', '-p', str(pid), '-o', 'pgid=,command='], capture_output=True, text=True, timeout=2, check=True).stdout.strip()
        group, command = out.split(None, 1)
        parts = command.split()
        return int(group) == pgid and '_worker' in parts and token in parts
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def elapsed(row, live, now):
    start, end = epoch(row.get('started_at')), epoch(row.get('finished_at'))
    if end is None and live:
        end = now
    return int(end - start) if start is not None and end is not None and end >= start else None


def book_checkpoint(root, package, source, fingerprint, dispatched, route):
    """Only this registered book's fresh, source-bound checkpoints; no page history replay."""
    candidates = []
    for relative in ('progress.json', 'evidence/progress-current.json', 'plan.json'):
        path = package / relative
        doc = read(root, path)
        if not doc:
            continue
        try:
            at = epoch(doc.get('updated_at')) or path.stat().st_mtime
        except OSError:
            continue
        origin = doc.get('source')
        origin = origin.get('path') if isinstance(origin, dict) else origin
        if dispatched is None or at < dispatched or origin != source or not fingerprint or doc.get('source_sha256') != fingerprint:
            continue
        reader = doc.get('reader_visual')
        stage = (reader.get('stage') if isinstance(reader, dict) else None) or doc.get('current_stage') or doc.get('stage') or doc.get('current_phase')
        counts = doc.get('page_completion') if isinstance(doc.get('page_completion'), dict) else doc
        done = counts.get('completed_pages', counts.get('processed_pages', counts.get('passed_count')))
        if isinstance(done, list):
            done = len(set(done)) if all(type(p) is int and p > 0 for p in done) else None
        total = number(doc.get('total_pages'))
        selected = doc.get('selected_pages')
        if isinstance(selected, list) and all(type(p) is int and p > 0 for p in selected):
            total = len(set(selected))
        done = number(done)
        if total is not None and done is not None and done > total:
            done = None
        candidates.append((at, {'phase': string(stage), 'done': done, 'total': total, 'checkpoint_at': datetime.fromtimestamp(at, timezone.utc).isoformat(), 'checkpoint_age_seconds': max(0, int(time.time() - at))}))
    # Reuse the official checkpoint convention, not a guessed percentage from filenames.
    directory = package / ('evidence/page-routes' if route == 'hybrid' else 'pages')
    newest = None
    try:
        if directory.resolve() == directory and directory.is_relative_to(root):
            with os.scandir(directory) as entries:
                for index, entry in enumerate(entries):
                    if index >= 10000:
                        newest = None
                        break
                    match = re.fullmatch(r'p([0-9]{3,9})\.json' if route == 'hybrid' else r'page-([0-9]{3,9})', entry.name)
                    if not match:
                        continue
                    path = Path(entry.path)
                    paths = (path,) if route == 'hybrid' else (path / 'summary.json', path / entry.name / 'summary.json')
                    for candidate in paths:
                        try:
                            if candidate.resolve() != candidate:
                                continue
                            at = candidate.stat().st_mtime
                        except OSError:
                            continue
                        if dispatched is not None and at >= dispatched and (newest is None or at > newest[0]):
                            newest = (at, int(match[1]), candidate)
    except (OSError, RuntimeError, ValueError):
        pass
    if newest:
        at, page, path = newest
        doc = read(root, path) or {}
        if fingerprint and doc.get('source_sha256') == fingerprint and doc.get('status') in ('passed', 'failed', 'needs-review') and (route != 'hybrid' or doc.get('page') == page):
            candidates.append((at, {'phase': 'page-processing', 'checkpoint_page': page, 'checkpoint_at': datetime.fromtimestamp(at, timezone.utc).isoformat(), 'checkpoint_age_seconds': max(0, int(time.time() - at))}))
    return max(candidates, key=lambda v: v[0])[1] if candidates else {}


def ledger_info(root):
    # The tracked repository documents the ledger path but has no schema/sample.
    doc = read(root, root / 'conversions/_consolidated/source-consolidation.json')
    return {'status': UNKNOWN, 'observed': doc is not None, 'updated_at': string(doc.get('updated_at')) if doc else None, 'reason': 'ledger-schema-unverified' if doc is not None else 'ledger-missing-or-unreadable'}


def project(state_root, output_root, run_id):
    base = state_root / run_id
    state_path = base / 'progress-current.json'
    state = read(state_root, state_path)
    result = {'run_id': run_id, 'status': UNKNOWN, 'recorded_status': None, 'active': None, 'route': None, 'updated_at': None, 'summary': None, 'strict_accepted_count': None, 'rows': [], 'reason': 'state-unavailable', 'resource_policy': None, 'halt_reason': None, 'error': None}
    if not state or state.get('run_id') != run_id or state.get('task_type') != 'pdf-collection' or state.get('status') not in STATES:
        return result
    recorded = state['status']
    active = worker_verified(state) if recorded in {'queued', 'running'} else False
    result.update(recorded_status=recorded, status=recorded if active or recorded not in {'queued', 'running'} else UNKNOWN, active=True if active else (None if recorded in {'queued', 'running'} else False), route=string(state.get('route')), updated_at=string(state.get('updated_at')), error=string(state.get('error')))
    policy = state.get('machine_resource_policy')
    if isinstance(policy, dict):
        result['resource_policy'] = {k: number(policy.get(k)) for k in ('local_pdf_capacity', 'visual_model_capacity')}
    result['disk_floor_bytes'] = number(state.get('disk_floor_bytes'))
    raw_root = string(state.get('run_root'))
    if not raw_root:
        return result
    batch = Path(raw_root)
    try:
        safe_batch = batch.is_absolute() and batch != output_root and batch.is_relative_to(output_root) and batch.resolve() == batch
    except (OSError, RuntimeError, ValueError):
        safe_batch = False
    if not safe_batch:
        result['reason'] = 'batch-root-not-allowed'
        return result
    frozen = read(state_root, base / 'source-list.txt', raw=True)
    sources = frozen.splitlines() if frozen else []
    if not sources or not all(Path(s).is_absolute() for s in sources) or len(set(sources)) != len(sources) or len(sources) != state.get('expected_sources'):
        result['reason'] = 'frozen-source-list-invalid'
        return result
    result['rows'] = [{'source': s, 'status': UNKNOWN} for s in sources]
    progress_path = batch / 'progress-current.json'
    progress, manifest, results = (read(output_root, batch / name) for name in ('progress-current.json', 'manifest.json', 'results.json'))
    if not progress or not manifest or not results:
        result['reason'] = 'batch-evidence-unavailable'
        return result
    generation = progress.get('updated_at')
    when, activation = epoch(generation), epoch(state.get('resumed_at') or state.get('started_at'))
    if when is None or activation is None or when < activation or manifest.get('updated_at') != generation or results.get('updated_at') != generation or not manifest.get('batch_id') or manifest.get('batch_id') != results.get('batch_id'):
        result['reason'] = 'stale-or-mixed-generation'
        return result
    inputs, items, registrations, summary = manifest.get('inputs'), results.get('items'), manifest.get('run_roots'), progress.get('summary')
    if not isinstance(inputs, list) or not isinstance(items, list) or not isinstance(registrations, dict) or not isinstance(summary, dict):
        result['reason'] = 'invalid-batch-schema'
        return result
    if any(not isinstance(i, dict) or not isinstance(i.get('source_path'), str) for i in inputs + items):
        result['reason'] = 'invalid-source-rows'
        return result
    ordered = [i['source_path'] for i in inputs]
    indexed = {i['source_path']: i for i in items}
    if len(ordered) != len(sources) or set(ordered) != set(sources) or len(indexed) != len(items) or not set(indexed).issubset(sources) or summary.get('planned_total') != len(sources):
        result['reason'] = 'source-registration-mismatch'
        return result
    running_sources = [i['source_path'] for i in items if i.get('status') == 'running']
    if len(running_sources) > 1 or (running_sources and progress.get('active_source') != running_sources[0]) or (not running_sources and progress.get('active_source') is not None):
        result['reason'] = 'active-source-mismatch'
        return result
    rows, strict = [], 0
    now = time.time()
    for source in ordered:
        item, registration = indexed.get(source), registrations.get(source)
        if item is None:
            rows.append({'source': source, 'status': 'pending' if active else UNKNOWN})
            continue
        if not isinstance(registration, dict) or not string(registration.get('source_sha256')) or item.get('source_sha256') != registration['source_sha256'] or item.get('run_root') != registration.get('path'):
            result['reason'] = 'package-registration-mismatch'
            return result
        raw_package = string(registration.get('path'))
        package = Path(raw_package) if raw_package else batch
        if package == batch or not package.is_relative_to(batch) or package.resolve() != package:
            result['reason'] = 'package-root-not-allowed'
            return result
        status = item.get('status')
        live = active and progress.get('active_source') == source and status == 'running'
        accepted = status == 'passed' and item.get('accepted') is True and item.get('full_pdf_coverage') is True
        strict += int(accepted)
        row = {'source': source, 'status': status if status in {'pending', 'interrupted', 'passed', 'needs-review', 'failed'} or live else UNKNOWN, 'route': string(item.get('route')), 'strict_in_batch': accepted if status in {'failed', 'needs-review'} or (status == 'passed' and type(item.get('accepted')) is bool and type(item.get('full_pdf_coverage')) is bool) else None, 'phase': string(item.get('final_stage')) if status != 'running' else None, 'done': None, 'total': number(item.get('total_pages')), 'elapsed_seconds': elapsed(item, live, now), 'error': string(item.get('error')), 'started_at': string(item.get('started_at')), 'finished_at': string(item.get('finished_at'))}
        if accepted:
            row['done'] = row['total']
        if live:
            row.update(book_checkpoint(output_root, package, source, registration['source_sha256'], when, result['route']))
        rows.append(row)
    # Re-read publication barriers, including outer lifecycle; no mixed/resumed generations.
    if read(output_root, progress_path) != progress or read(state_root, state_path) != state:
        result['reason'] = 'snapshot-changed-during-read'
        return result
    counts = {k: number(summary.get(k)) for k in ('planned_total', 'passed', 'failed', 'needs_review', 'running', 'pending_total', 'completed_total')}
    if number(progress.get('strict_accepted_count')) != strict or any(counts[k] != sum(i.get('status') == status for i in items) for k, status in [('passed','passed'), ('failed','failed'), ('needs_review','needs-review'), ('running','running')]):
        result['reason'] = 'batch-count-mismatch'
        return result
    if counts['completed_total'] != counts['passed'] + counts['failed'] + counts['needs_review'] or counts['pending_total'] != len(sources) - counts['completed_total']:
        result['reason'] = 'batch-terminal-count-mismatch'
        return result
    result.update(rows=rows, summary=counts, strict_accepted_count=strict, batch_updated_at=generation, active_source=progress.get('active_source') if active else None, halt_reason=string(progress.get('halt_reason')), reason=None if active or recorded not in {'queued', 'running'} else 'worker-identity-unverified')
    return result


class Observer:
    def __init__(self, state_root, output_root, run_ids):
        self.state_root, self.output_root, self.run_ids = state_root, output_root, run_ids
        self.epoch, self.seq, self.cached, self.last = uuid.uuid4().hex, 0, None, 0
        self.lock = threading.Lock()

    def snapshot(self):
        with self.lock:
            if self.cached is not None and time.monotonic() - self.last < 2:
                return self.cached
            batches = []
            for run_id in self.run_ids:
                try:
                    batches.append(project(self.state_root, self.output_root, run_id))
                except (OSError, TypeError, ValueError, RuntimeError, OverflowError):
                    batches.append({'run_id': run_id, 'status': UNKNOWN, 'rows': [], 'reason': 'invalid-evidence'})
            try:
                disk = shutil.disk_usage(self.output_root)._asdict()
            except OSError:
                disk = None
            self.seq += 1
            self.cached = {'schema': 'd2md-workbench-r3', 'epoch': self.epoch, 'sequence': self.seq, 'observed_at': stamp(), 'batches': batches, 'ledger': ledger_info(self.output_root), 'disk': disk, 'slot_occupancy': UNKNOWN}
            self.last = time.monotonic()
            return self.cached


class Server(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, observer):
        super().__init__(address, Handler)
        self.observer = observer
        self.streams = threading.BoundedSemaphore(8)
        self.origin = f'http://127.0.0.1:{self.server_port}'


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def headers_for(self, mime, length=None):
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        if length is not None:
            self.send_header('Content-Length', str(length))
        self.end_headers()

    def do_GET(self):
        if self.headers.get('Host') != self.server.origin.removeprefix('http://') or self.headers.get('Origin') not in (None, self.server.origin) or self.headers.get('Sec-Fetch-Site') == 'cross-site':
            self.send_error(403)
            return
        if self.path == '/api/snapshot':
            data = json.dumps(self.server.observer.snapshot(), ensure_ascii=False).encode()
            self.headers_for('application/json; charset=utf-8', len(data))
            self.wfile.write(data)
        elif self.path == '/api/events':
            if not self.server.streams.acquire(blocking=False):
                self.send_error(503)
                return
            try:
                self.headers_for('text/event-stream; charset=utf-8')
                while True:
                    snapshot = self.server.observer.snapshot()
                    data = json.dumps(snapshot, ensure_ascii=False, separators=(',', ':'))
                    self.wfile.write(f"id: {snapshot['epoch']}:{snapshot['sequence']}\nretry: 2000\ndata: {data}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(3)
            except (OSError, TimeoutError):
                pass
            finally:
                self.server.streams.release()
        elif self.path in ('/', '/app.js', '/style.css'):
            name, mime = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}[self.path]
            data = (HERE / name).read_bytes()
            self.headers_for(mime + '; charset=utf-8', len(data))
            self.wfile.write(data)
        else:
            self.send_error(404)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-root', type=Path, default=Path.home() / '.codex/background-tasks')
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--run-id', action='append', required=True, help='registered pdf-collection ID; repeat for multiple batches')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if len(args.run_id) > 16 or any(not RUN_ID.fullmatch(i) for i in args.run_id) or len(set(args.run_id)) != len(args.run_id):
        parser.error('invalid, duplicate or too many run IDs')
    state_root, output_root = args.state_root.expanduser().resolve(), args.output_root.expanduser().resolve()
    if not state_root.is_dir() or not output_root.is_dir():
        parser.error('existing state and output roots required')
    with Server(('127.0.0.1', args.port), Observer(state_root, output_root, args.run_id)) as server:
        print(server.origin, flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()

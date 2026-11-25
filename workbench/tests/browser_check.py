"""Real Chromium over loopback HTTP/SSE, isolated fixture. Requires Playwright."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from test_readonly import Fixture

HERE = Path(__file__).resolve().parents[1]

with tempfile.TemporaryDirectory() as tmp:
    f = Fixture(Path(tmp).resolve())
    before = f.hashes()
    args = [sys.executable, str(HERE/'server.py'), '--state-root',str(f.state_root),'--output-root',str(f.output),'--run-id',f.run_id,'--port','0']
    service = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        origin=service.stdout.readline().strip()
        assert origin.startswith('http://127.0.0.1:')
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH') or None,args=['--no-sandbox'])
            context=browser.new_context(viewport={'width':1180,'height':850})
            page=context.new_page(); errors=[];page.on('pageerror',lambda e: errors.append(str(e)))
            page.goto(origin,wait_until='domcontentloaded')
            expect(page.locator('#connection')).to_contain_text('已连接',timeout=10000)
            expect(page.locator('#tasks .task')).to_have_count(3)
            expect(page.locator('#tasks')).to_contain_text('/input/a/同名.pdf')
            expect(page.locator('#tasks')).to_contain_text('/input/b/同名.pdf')
            expect(page.locator('#ledger')).to_contain_text('Unknown')
            expect(page.get_by_role('button',name='暂停',exact=True)).to_be_disabled()
            page.locator('#search').fill('/input/b/');expect(page.locator('#tasks .task')).to_have_count(1)
            page.locator('#search').fill('');page.locator('#tasks details summary').first.click()
            page.wait_for_timeout(3500);expect(page.locator('#tasks details').first).to_have_attribute('open','')
            page.get_by_role('button',name='资源与清理').click();expect(page.locator('#resources')).to_contain_text('GiB');expect(page.get_by_role('button',name='删除文件')).to_be_disabled()
            page.get_by_role('button',name='转换队列').click()
            page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.reload(wait_until='domcontentloaded');expect(page.locator('#connection')).to_contain_text('已连接',timeout=10000);expect(page.locator('#tasks .task')).to_have_count(3)
            context.set_offline(True);expect(page.locator('#connection')).to_contain_text('非实时',timeout=16000)
            context.set_offline(False);expect(page.locator('#connection')).to_contain_text('已连接',timeout=16000)
            page.get_by_role('button',name='重新连接').click();expect(page.locator('#connection')).to_contain_text('已连接',timeout=10000)
            assert not errors,errors
            browser.close()
        assert f.hashes()==before,'fixture evidence changed'
        print('PASS: real HTTP/SSE, distinct sources, Unknown, disabled controls, search, details, resources, 390px layout, reload, network loss/recovery, manual reconnect, no JS errors, evidence hashes unchanged')
    finally:
        service.terminate();service.wait(timeout=5)

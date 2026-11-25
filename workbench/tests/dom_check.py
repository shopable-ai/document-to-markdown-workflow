"""Chromium DOM-only fallback: no network, explicit fake EventSource in test only."""
import json
import os
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from test_readonly import Fixture, server

HERE = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as tmp:
    f = Fixture(Path(tmp).resolve())
    snapshot = server.Observer(f.state_root,f.output,[f.run_id]).snapshot()
    before=f.hashes()
    # Load the actual UI source in memory; never claim this tests browser HTTP/SSE.
    html=(HERE/'index.html').read_text().replace('<link rel="stylesheet" href="/style.css">','<style>'+(HERE/'style.css').read_text()+'</style>').replace('<script src="/app.js" defer></script>','')
    harness="""window.testStreams=[]; window.EventSource=class {constructor(url){this.url=url;testStreams.push(this);}close(){this.closed=true;}}; window.emitSnapshot=(s)=>testStreams.at(-1).onmessage({data:JSON.stringify(s)}); window.emitError=()=>testStreams.at(-1).onerror();"""
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH') or None,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1180,'height':850}); errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.set_content(html);page.add_script_tag(content=harness);page.add_script_tag(content=(HERE/'app.js').read_text());page.evaluate('(s)=>emitSnapshot(s)',snapshot)
        expect(page.locator('#connection')).to_contain_text('已连接');expect(page.locator('#tasks .task')).to_have_count(3)
        expect(page.locator('#tasks')).to_contain_text('/input/a/同名.pdf');expect(page.locator('#tasks')).to_contain_text('/input/b/同名.pdf');expect(page.locator('#ledger')).to_contain_text('Unknown')
        expect(page.get_by_role('button',name='暂停',exact=True)).to_be_disabled()
        page.locator('#search').fill('/input/b/');expect(page.locator('#tasks .task')).to_have_count(1);page.locator('#search').fill('')
        page.locator('#tasks details summary').first.click();snapshot['sequence']+=1;page.evaluate('(s)=>emitSnapshot(s)',snapshot);expect(page.locator('#tasks details').first).to_have_attribute('open','')
        page.locator('#filter').select_option('passed');expect(page.locator('#tasks .task')).to_have_count(1);page.locator('#filter').select_option('')
        page.get_by_role('button',name='资源与清理').click();expect(page.locator('#resources')).to_contain_text('GiB');expect(page.get_by_role('button',name='删除文件')).to_be_disabled()
        page.get_by_role('button',name='转换队列').click();page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.evaluate('emitError()');expect(page.locator('#connection')).to_contain_text('非实时');page.get_by_role('button',name='重新连接').click();assert page.evaluate('testStreams.length===2&&testStreams[0].closed')
        snapshot['sequence']+=1;page.evaluate('(s)=>emitSnapshot(s)',snapshot);expect(page.locator('#connection')).to_contain_text('已连接');assert not errors,errors
        browser.close()
    assert f.hashes()==before
    print('PASS: Chromium DOM-only; rendering, same-name identity, Unknown, disabled actions, search/filter, expanded details, disk tab, 390px layout, simulated disconnect and recovery, no JS errors. NOT browser HTTP/SSE.')

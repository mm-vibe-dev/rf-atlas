"""Offline public browser smoke: no receiver startup or private data."""
from pathlib import Path
import sys
import os
import tempfile
import threading
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rfatlas.config import Config
from rfatlas.runtime import RuntimeSupervisor
from rfatlas.dashboard import create_dashboard_server
from playwright.sync_api import sync_playwright


def main():
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder);(root/'data').mkdir()
        runtime=RuntimeSupervisor(Config(root))
        server=create_dashboard_server(root,runtime=runtime)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();errors=[]
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True,channel=os.environ.get('RFATLAS_BROWSER_CHANNEL'));page=browser.new_page()
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{server.server_port}',wait_until='networkidle')
                assert page.locator('#mainnav button').all_text_contents()==['Explore','Signals','Settings']
                page.locator('#mainnav [data-nav=settings]').click()
                assert not page.locator('[name=ai_enabled]').is_checked()
                assert not page.locator('[name=retain_frames]').is_checked()
                page.locator('[name=ai_enabled]').check();page.locator('#settingsform button').click()
                page.wait_for_function("() => document.querySelector('[name=ai_enabled]').checked")
                page.set_viewport_size(dict(width=390,height=844))
                assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
                assert runtime.status()['mode']=='idle';browser.close()
            assert not errors,errors
            print('Offline desktop/mobile browser smoke passed; no receiver operations')
        finally:
            server.shutdown();server.server_close();thread.join(3);runtime.close()


if __name__=='__main__':main()

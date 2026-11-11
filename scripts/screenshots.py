"""Generates the documentation screenshots in docs/images/.

Runs the app against the simulated OPNsense from the test suite (no real
firewall, no real data) and captures the UI with Playwright.

    pip install -r backend/requirements-dev.txt playwright
    python scripts/screenshots.py            # uses the installed Google Chrome
"""

import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from tests import configs  # noqa: E402
from tests.fake_opnsense import FakeOpnsense  # noqa: E402

PORT = 8765
TOKEN = "screenshot-token-0123456789"
OUT = ROOT / "docs" / "images"

HOSTS = [
    ("192.168.1.20", "aa:bb:cc:00:00:20", "living-room-tv"),
    ("192.168.1.21", "aa:bb:cc:00:00:21", "kitchen-radio"),
    ("192.168.1.22", "aa:bb:cc:00:00:22", "game-console"),
    ("192.168.1.30", "aa:bb:cc:00:00:30", "work-laptop"),
    ("192.168.1.31", "aa:bb:cc:00:00:31", "tablet"),
    ("192.168.1.40", "aa:bb:cc:00:00:40", "smart-speaker"),
]


def make_fake() -> FakeOpnsense:
    fake = FakeOpnsense()
    fake.discovery = [
        {"source": "discovery", "interface_name": "LAN", "ether_address": mac, "ip_address": ip,
         "organization_name": "", "first_seen": "2026-11-01T10:00:00Z", "last_seen": "2026-11-11T10:00:00Z"}
        for ip, mac, _ in HOSTS[:-1]
    ]
    fake.leases = [{"address": ip, "hwaddr": mac, "hostname": name} for ip, mac, name in HOSTS]
    fake.arp = []
    return fake


def serve(fake: FakeOpnsense, data_dir: Path) -> uvicorn.Server:
    settings = Settings(opnsense_url="https://opnsense.example", api_key="x", api_secret="x",
                        verify_tls=True, api_token=TOKEN, data_dir=data_dir, verify_timeout=1)
    server = uvicorn.Server(uvicorn.Config(create_app(settings, transport=fake.transport()),
                                           host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.1)
    return server


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fake = make_fake()
    with tempfile.TemporaryDirectory() as tmp:
        server = serve(fake, Path(tmp))
        base = f"http://127.0.0.1:{PORT}"
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 860}, device_scale_factor=2, locale="en-US")
            page.add_init_script(f"localStorage.setItem('vpnmgr.token', '{TOKEN}')")
            api = page.request

            def create(name: str, config: str) -> None:
                r = api.post(f"{base}/api/vpns", data={"name": name, "config": config},
                             headers={"Authorization": f"Bearer {TOKEN}"})
                job = r.json()["job_id"]
                while api.get(f"{base}/api/jobs/{job}", headers={"Authorization": f"Bearer {TOKEN}"}).json()["status"] == "running":
                    time.sleep(0.2)

            # Sign-in page
            page.context.clear_cookies()
            login = browser.new_page(viewport={"width": 1280, "height": 600}, device_scale_factor=2, locale="en-US")
            login.goto(base)
            login.screenshot(path=OUT / "login.png")
            login.close()

            # Add a VPN through the UI to capture the step log
            page.goto(base)
            page.fill("input[name=name]", "tv_usa")
            page.fill("textarea[name=config]", configs.PROTON.replace("185.159.157.1", "149.40.50.1"))
            page.wait_for_selector("text=Endpoint")
            page.screenshot(path=OUT / "add-vpn-preview.png", full_page=True)
            page.click("button:has-text('Create VPN')")
            page.wait_for_selector("text=succeeded", timeout=30000)
            page.screenshot(path=OUT / "add-vpn-result.png", full_page=True)

            # Second VPN (same Proton address -> rewritten automatically)
            create("radio_uk", configs.PROTON_V6.replace("185.159.157.1", "146.70.179.1"))

            # Assign hosts
            api.put(f"{base}/api/hosts/assignments", headers={"Authorization": f"Bearer {TOKEN}"},
                    data=[{"ip": "192.168.1.20", "vpn": "tv_usa"}, {"ip": "192.168.1.22", "vpn": "tv_usa"},
                          {"ip": "192.168.1.21", "vpn": "radio_uk"}])

            page.goto(base)
            page.wait_for_selector("text=radio_uk")
            page.screenshot(path=OUT / "vpns.png", full_page=True)

            page.goto(f"{base}/hosts")
            page.wait_for_selector("text=living-room-tv")
            page.screenshot(path=OUT / "hosts.png", full_page=True)

            page.goto(f"{base}/system")
            page.wait_for_selector("text=Privileges")
            page.screenshot(path=OUT / "system.png", full_page=True)
            browser.close()
        server.should_exit = True
    print("Screenshots written to", OUT)


if __name__ == "__main__":
    main()

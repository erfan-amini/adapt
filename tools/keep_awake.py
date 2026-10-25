"""Open the deployed app in a headless browser so Streamlit Community Cloud
does not put it to sleep, and wake it if it already has.

Community Cloud hibernates an app after about 12 hours without a visitor.
A plain HTTP request is not a visit; a browser session is. This script is run
by .github/workflows/keep_awake.yml every 6 hours.

    python tools/keep_awake.py https://adapt2026.streamlit.app/
"""
import sys
import time

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "https://adapt2026.streamlit.app/"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(URL, wait_until="domcontentloaded", timeout=120_000)
    time.sleep(8)
    wake = page.get_by_role("button", name="Yes, get this app back up")
    if wake.count():
        print("App was asleep: waking it.")
        wake.first.click()
        for _ in range(36):                 # up to 3 minutes for the container to start
            time.sleep(5)
            if "ADAPT" in page.title():
                break
    else:
        print("App is awake.")
    time.sleep(30)                          # keep the session open long enough to count as a visit
    print("Title:", page.title())
    browser.close()

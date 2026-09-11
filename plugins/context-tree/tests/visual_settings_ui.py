import os
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "tests" / "artifacts"
ARTIFACTS.mkdir(parents=True, exist_ok=True)
URL = os.environ.get("CONTEXT_TREE_UI_URL", "http://127.0.0.1:18765/")
CHROME = os.environ.get(
    "CONTEXT_TREE_CHROME",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
)


def assert_no_overlap(page) -> None:
    header = page.locator(".app-header").bounding_box()
    shell = page.locator(".shell").bounding_box()
    assert header and shell
    assert header["y"] + header["height"] <= shell["y"] + 1
    rows = page.locator(".topic-row").evaluate_all(
        """elements => elements.map(row => {
            const box = row.getBoundingClientRect();
            const button = row.querySelector('.select-topic').getBoundingClientRect();
            return {box: {x: box.x, width: box.width, height: box.height},
                    button: {x: button.x, width: button.width, height: button.height}};
        })"""
    )
    for row in rows:
        assert row["box"]["height"] >= 70
        assert row["button"]["height"] > 0
        assert row["button"]["x"] + row["button"]["width"] <= row["box"]["x"] + row["box"]["width"] + 1


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, executable_path=CHROME)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(URL)
    page.wait_for_load_state("networkidle")
    assert page.locator("#usage-server").is_visible()
    assert page.locator("#backfill-session").is_visible()
    page.get_by_role("button", name="新建主题").click()
    page.locator("#topic-title").fill("本地主题切换")
    page.locator("#topic-summary").fill("不经过 AI 修改主题和整理频率")
    page.locator("#topic-form").get_by_role("button", name="创建").click()
    topic_row = page.locator(".topic-row", has_text="本地主题切换").first
    topic_row.wait_for()
    topic_row.locator(".select-topic").click()
    page.wait_for_function("document.querySelector('#selection-name').textContent === '本地主题切换'")
    assert page.locator("#selection-name").inner_text() == "本地主题切换"
    page.locator("#mode-auto").click()
    page.wait_for_function("document.querySelector('#selection-name').textContent === '自动判断'")
    page.locator("#intervals button[data-value='5']").click()
    page.wait_for_function("document.querySelector('#intervals button[data-value=\"5\"]').classList.contains('active')")
    assert page.locator("#selection-name").inner_text() == "自动判断"
    assert "active" in (page.locator("#intervals button[data-value='5']").get_attribute("class") or "")
    assert_no_overlap(page)
    page.screenshot(path=str(ARTIFACTS / "settings-backfill-desktop.png"), full_page=True)

    mobile = browser.new_page(viewport={"width": 390, "height": 844})
    mobile.goto(URL)
    mobile.wait_for_load_state("networkidle")
    assert mobile.locator("#usage-server").is_visible()
    assert mobile.locator("#backfill-session").is_visible()
    mobile.locator(".topic-title strong", has_text="本地主题切换").first.wait_for()
    assert_no_overlap(mobile)
    mobile.screenshot(path=str(ARTIFACTS / "settings-backfill-mobile.png"), full_page=True)
    browser.close()

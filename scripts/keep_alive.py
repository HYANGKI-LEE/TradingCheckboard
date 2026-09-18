"""Streamlit Community Cloud는 12시간 동안 실제 방문(브라우저 세션)이 없으면 앱을 재운다.
단순 HTTP GET(curl 등)은 방문으로 인정되지 않으므로, 헤드리스 브라우저로 실제 페이지를 열어
방문 세션을 만들고, 잠들어 있으면 "깨우기" 버튼까지 눌러준다."""

from playwright.sync_api import sync_playwright

URL = "https://tradingcheckboard.streamlit.app"


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(URL, wait_until="load", timeout=60000)
        page.wait_for_timeout(4000)

        # 잠들어 있으면 "Zzzz..." 안내와 함께 "Yes, get this app back up!" 버튼이 뜬다.
        # 실행 중인 앱의 일반 버튼(기간 선택 등)은 건드리지 않도록 깨우기 버튼만 골라서 클릭.
        wake_button = page.get_by_role("button", name="get this app back up", exact=False)
        if wake_button.count() > 0:
            wake_button.first.click()
            page.wait_for_timeout(20000)

        browser.close()


if __name__ == "__main__":
    main()

"""
미국 주식/경제 뉴스를 RSS에서 가져와 index.html 페이지를 만드는 스크립트.
GitHub Actions가 매시간 이 스크립트를 자동으로 실행합니다.
"""

import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import html

# 여기에 RSS 주소를 추가/삭제하면 뉴스 소스를 바꿀 수 있습니다.
# 하나가 실패해도 나머지는 계속 진행되도록 만들었습니다.
RSS_FEEDS = [
    {"name": "CNBC", "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
]

MAX_ITEMS_PER_FEED = 15


def fetch_feed(feed):
    """RSS 주소 하나를 가져와서 [{title, link, pubDate}, ...] 리스트로 반환."""
    items = []
    try:
        req = urllib.request.Request(
            feed["url"],
            headers={"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
        for item in root.findall(".//item")[:MAX_ITEMS_PER_FEED]:
            title = item.findtext("title", default="").strip()
            link = item.findtext("link", default="").strip()
            pub_date = item.findtext("pubDate", default="").strip()
            if title and link:
                items.append({"title": title, "link": link, "pubDate": pub_date})
    except Exception as e:
        # 이 소스가 실패해도 전체 스크립트는 죽지 않고 계속 진행됩니다.
        print(f"[경고] {feed['name']} 가져오기 실패: {e}")
    return items


def build_html(all_results):
    """가져온 뉴스로 index.html 문자열을 만듭니다."""
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    sections_html = ""
    for feed_name, items in all_results.items():
        if not items:
            continue
        rows = ""
        for it in items:
            title = html.escape(it["title"])
            link = html.escape(it["link"])
            pub = html.escape(it["pubDate"])
            rows += f"""
            <li class="item">
              <a href="{link}" target="_blank" rel="noopener">{title}</a>
              <div class="meta">{pub}</div>
            </li>"""
        sections_html += f"""
        <section class="feed-section">
          <h2>{html.escape(feed_name)}</h2>
          <ul class="item-list">{rows}
          </ul>
        </section>"""

    if not sections_html:
        sections_html = "<p>지금은 가져온 뉴스가 없어요. 다음 자동 갱신을 기다려주세요.</p>"

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>미국 경제뉴스 자동 요약</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #f5f6fa;
    color: #202124;
    margin: 0;
    padding: 20px 16px 60px;
  }}
  .wrap {{ max-width: 760px; margin: 0 auto; }}
  h1 {{ font-size: 24px; margin: 0 0 4px; }}
  .updated {{ color: #777; font-size: 13px; margin-bottom: 24px; }}
  .feed-section {{ margin-bottom: 28px; }}
  h2 {{ font-size: 18px; border-bottom: 2px solid #5b63d3; padding-bottom: 6px; }}
  .item-list {{ list-style: none; padding: 0; margin: 0; }}
  .item {{
    background: white;
    border-radius: 12px;
    padding: 14px 16px;
    margin: 8px 0;
    box-shadow: 0 1px 4px rgba(0,0,0,.06);
  }}
  .item a {{
    color: #202124;
    text-decoration: none;
    font-size: 15px;
    font-weight: 600;
    line-height: 1.4;
  }}
  .item a:hover {{ color: #5b63d3; }}
  .meta {{ color: #999; font-size: 12px; margin-top: 6px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>📈 미국 경제뉴스 자동 요약</h1>
  <div class="updated">마지막 업데이트: {now_utc} (1시간마다 자동 갱신)</div>
  {sections_html}
</div>
</body>
</html>
"""


def main():
    all_results = {}
    for feed in RSS_FEEDS:
        all_results[feed["name"]] = fetch_feed(feed)

    html_out = build_html(all_results)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_out)
    print("index.html 생성 완료")


if __name__ == "__main__":
    main()

"""
전세계 주요 언론사에서 경제/주식 뉴스를 RSS로 가져와 index.html 페이지를 만드는 스크립트.
- 여러 언론사가 동시에 다루는 뉴스는 "주요 뉴스"로 자동 감지해서 맨 위에 표시
- 나머지는 카테고리별로 분류
GitHub Actions가 매시간 이 스크립트를 자동으로 실행합니다.
"""

import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import html
import time
import re
from deep_translator import GoogleTranslator

# 여기에 RSS 주소를 추가/삭제하면 뉴스 소스를 바꿀 수 있습니다.
# 하나가 실패해도 나머지는 계속 진행되도록 만들었습니다.
RSS_FEEDS = [
    {"name": "CNBC", "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
    {"name": "MarketWatch", "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories"},
    {"name": "BBC", "url": "https://feeds.bbci.co.uk/news/business/rss.xml"},
    {"name": "NPR", "url": "https://feeds.npr.org/1006/rss.xml"},
    {"name": "The Guardian", "url": "https://www.theguardian.com/business/rss"},
    {"name": "Business Insider", "url": "https://feeds.businessinsider.com/custom/all"},
]

MAX_ITEMS_PER_FEED = 10

# 카테고리 분류 규칙: 위에서부터 순서대로 검사해서 먼저 맞는 걸로 분류합니다.
CATEGORY_RULES = [
    ("전쟁·지정학", ["war", "houthi", "iran", "ukraine", "russia", "attack",
                    "military", "ceasefire", "sanction", "missile", "conflict",
                    "troops", "strike on"]),
    ("유가·원자재", ["oil", "crude", "opec", "gold", "gas price", "commodity",
                    "barrel", "brent", "wti", "natural gas"]),
    ("금리·연준", ["fed ", "federal reserve", "rate hike", "rate cut",
                  "interest rate", "inflation", "cpi", "jobs report",
                  "unemployment", "central bank", "yield", "bank of japan",
                  "bank of korea", "ecb "]),
    ("AI·테크", ["ai ", " ai", "openai", "anthropic", "artificial intelligence",
                "chip", "nvidia", "software", "tech "]),
    ("주식·증시", ["stock", "share", "nasdaq", "dow jones", "dow ", "s&p",
                  "ipo", "earnings", "market", "trading", "investor"]),
]
DEFAULT_CATEGORY = "기타"
CATEGORY_ORDER = [name for name, _ in CATEGORY_RULES] + [DEFAULT_CATEGORY]

# 클러스터링(같은 사건 감지)용 불용어 - 이 단어들은 핵심 단어로 안 침
STOPWORDS = {
    "the", "a", "an", "to", "of", "in", "on", "for", "with", "as", "is",
    "are", "at", "by", "its", "after", "from", "and", "or", "new", "says",
    "say", "said", "up", "down", "over", "this", "that", "will", "be",
    "has", "have", "had", "their", "his", "her", "not", "but", "how",
    "why", "what", "who", "into", "than", "amid", "amid", "could", "would",
    "should", "more", "most", "than", "now", "one", "two", "three",
}

_translator = GoogleTranslator(source="en", target="ko")


def translate_title(title):
    """영문 제목을 한국어로 번역. 실패하면 원문을 그대로 반환."""
    try:
        return _translator.translate(title)
    except Exception as e:
        print(f"[경고] 번역 실패 ({title[:30]}...): {e}")
        return title


def classify(title):
    """제목(영문) 안의 키워드를 보고 카테고리를 정합니다."""
    t = title.lower()
    for category, keywords in CATEGORY_RULES:
        for kw in keywords:
            if kw in t:
                return category
    return DEFAULT_CATEGORY


def significant_words(title):
    """제목에서 핵심 단어(4글자 이상, 불용어 제외)만 뽑아 집합으로 반환."""
    words = re.findall(r"[a-zA-Z']+", title.lower())
    return {w for w in words if len(w) > 3 and w not in STOPWORDS}


def cluster_items(items):
    """
    핵심 단어가 많이 겹치는, 서로 다른 언론사의 기사를 하나의 사건(클러스터)으로 묶습니다.
    같은 언론사끼리는 절대 묶지 않습니다(오탐 방지).
    """
    clusters = []
    for it in items:
        words = significant_words(it["title"])
        matched = None
        for c in clusters:
            sources_in_cluster = {x["source"] for x in c["items"]}
            if it["source"] in sources_in_cluster:
                continue
            overlap = words & c["words"]
            smaller = max(1, min(len(words), len(c["words"])))
            if len(overlap) >= 3 and len(overlap) / smaller >= 0.5:
                matched = c
                break
        if matched:
            matched["items"].append(it)
        else:
            clusters.append({"items": [it], "words": words})
    return clusters


def fetch_feed(feed):
    """RSS 주소 하나를 가져와서 뉴스 항목 리스트로 반환."""
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
                title_ko = translate_title(title)
                time.sleep(0.25)  # 번역 서비스에 너무 빠르게 요청하지 않도록 살짝 대기
                items.append({
                    "title": title,
                    "title_ko": title_ko,
                    "link": link,
                    "pubDate": pub_date,
                    "source": feed["name"],
                    "category": classify(title),
                })
    except Exception as e:
        # 이 소스가 실패해도 전체 스크립트는 죽지 않고 계속 진행됩니다.
        print(f"[경고] {feed['name']} 가져오기 실패: {e}")
    return items


def render_item(it):
    title_ko = html.escape(it["title_ko"])
    title_en = html.escape(it["title"])
    link = html.escape(it["link"])
    pub = html.escape(it["pubDate"])
    source = html.escape(it["source"])
    return f"""
    <li class="item">
      <a href="{link}" target="_blank" rel="noopener">{title_ko}</a>
      <div class="original">{title_en}</div>
      <div class="meta">{source} · {pub}</div>
    </li>"""


def render_top_cluster(cluster):
    main = cluster["items"][0]
    title_ko = html.escape(main["title_ko"])
    links_html = " · ".join(
        f'<a href="{html.escape(it["link"])}" target="_blank" rel="noopener">{html.escape(it["source"])}</a>'
        for it in cluster["items"]
    )
    n = len(cluster["items"])
    return f"""
    <li class="item top-item">
      <div class="top-title">{title_ko}</div>
      <div class="meta">{n}개 언론사 보도: {links_html}</div>
    </li>"""


def build_html(all_items):
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    clusters = cluster_items(all_items)
    top_clusters = sorted(
        [c for c in clusters if len(c["items"]) >= 2],
        key=lambda c: len(c["items"]),
        reverse=True,
    )
    single_items = [c["items"][0] for c in clusters if len(c["items"]) == 1]

    sections_html = ""

    if top_clusters:
        rows = "".join(render_top_cluster(c) for c in top_clusters)
        sections_html += f"""
        <section class="feed-section top-section">
          <h2>🔥 주요 뉴스 (여러 언론사 동시 보도)</h2>
          <ul class="item-list">{rows}
          </ul>
        </section>"""

    grouped = {}
    for it in single_items:
        grouped.setdefault(it["category"], []).append(it)

    for category in CATEGORY_ORDER:
        items = grouped.get(category)
        if not items:
            continue
        rows = "".join(render_item(it) for it in items)
        sections_html += f"""
        <section class="feed-section">
          <h2>{html.escape(category)}</h2>
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
  .top-section h2 {{ border-bottom-color: #e0507a; }}
  .item-list {{ list-style: none; padding: 0; margin: 0; }}
  .item {{
    background: white;
    border-radius: 12px;
    padding: 14px 16px;
    margin: 8px 0;
    box-shadow: 0 1px 4px rgba(0,0,0,.06);
  }}
  .top-item {{ border-left: 4px solid #e0507a; }}
  .top-title {{ font-size: 15px; font-weight: 700; line-height: 1.4; }}
  .item a {{
    color: #202124;
    text-decoration: none;
    font-size: 15px;
    font-weight: 600;
    line-height: 1.4;
  }}
  .item a:hover {{ color: #5b63d3; }}
  .original {{ color: #888; font-size: 12.5px; margin-top: 4px; }}
  .meta {{ color: #999; font-size: 12px; margin-top: 6px; }}
  .meta a {{ color: #5b63d3; font-weight: 600; text-decoration: none; }}
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
    all_items = []
    for feed in RSS_FEEDS:
        all_items.extend(fetch_feed(feed))

    html_out = build_html(all_items)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_out)
    print("index.html 생성 완료")


if __name__ == "__main__":
    main()

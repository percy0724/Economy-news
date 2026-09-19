"""
해외 주요 언론사의 경제/증시 뉴스를 RSS로 모아 index.html을 만드는 스크립트.

주요 기능
- 여러 언론사가 동시에 다루는 뉴스는 "주요 뉴스"로 묶어서 맨 위에 표시
- 나머지는 카테고리별로 분류
- 제목을 한국어로 번역 (DeepL 우선, 실패하면 구글 번역으로 자동 대체)
- 각 기사에 "요약 + 영향" 보기를 붙임 (완전 무료: AI 호출 없음)
  - 요약: 언론사가 RSS에 이미 넣어준 소개글(description)을 그대로 사용
  - 영향: 카테고리별로 미리 써둔 일반적인 설명 (기사마다 다르진 않음)
- 시간은 한국 시간(KST)으로 표시

GitHub Actions가 매시간 자동 실행합니다.

번역 키 설정 (선택이지만 권장)
  저장소 Settings > Secrets and variables > Actions 에서
  DEEPL_KEY 라는 이름으로 DeepL Free 인증키를 등록하세요.
  키가 없으면 구글 번역으로 동작합니다.
"""

import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# ---------------------------------------------------------------- 설정

# RSS 주소를 추가/삭제하면 뉴스 소스가 바뀝니다.
# 하나가 실패해도 나머지는 계속 진행됩니다.
RSS_FEEDS = [
    {"name": "CNBC", "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
    {"name": "MarketWatch", "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories"},
    {"name": "BBC", "url": "https://feeds.bbci.co.uk/news/business/rss.xml"},
    {"name": "Fortune", "url": "https://fortune.com/feed"},
    {"name": "The Guardian", "url": "https://www.theguardian.com/business/rss"},
    {"name": "Al Jazeera", "url": "https://www.aljazeera.com/xml/rss/all.xml"},
]

MAX_ITEMS_PER_FEED = 10

KST = timezone(timedelta(hours=9))

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# 카테고리 분류 규칙. 위에서부터 순서대로 검사해 먼저 맞는 것으로 정합니다.
CATEGORY_RULES = [
    ("전쟁·지정학", [
        r"\bwar\b", r"\bwarfare\b", r"houthi", r"\biran\b", r"ukraine", r"russia",
        r"\battacks?\b", r"military", r"ceasefire", r"sanctions?\b", r"missiles?\b",
        r"conflict", r"troops", r"airstrike", r"\bnato\b", r"invasion",
    ]),
    ("유가·원자재", [
        r"\boil\b", r"crude", r"\bopec\b", r"\bgold\b", r"gas price", r"commodit",
        r"barrels?\b", r"\bbrent\b", r"\bwti\b", r"natural gas", r"copper",
    ]),
    ("금리·연준", [
        r"\bfed\b", r"federal reserve", r"rate (hike|cut|decision)", r"interest rates?\b",
        r"inflation", r"\bcpi\b", r"jobs report", r"unemployment", r"central bank",
        r"\byields?\b", r"bank of japan", r"bank of korea", r"\becb\b", r"treasur",
    ]),
    ("AI·테크", [
        r"\bai\b", r"openai", r"anthropic", r"artificial intelligence", r"\bchips?\b",
        r"nvidia", r"software", r"semiconductor", r"data cent(er|re)",
    ]),
    ("주식·증시", [
        r"\bstocks?\b", r"\bshares?\b", r"nasdaq", r"dow jones", r"\bs&p\b", r"\bipo\b",
        r"earnings", r"\bmarkets?\b", r"trading", r"investors?\b", r"wall street",
    ]),
]
DEFAULT_CATEGORY = "기타"
CATEGORY_ORDER = [name for name, _ in CATEGORY_RULES] + [DEFAULT_CATEGORY]

COMPILED_RULES = [
    (name, [re.compile(p, re.IGNORECASE) for p in patterns])
    for name, patterns in CATEGORY_RULES
]

# 카테고리별 "영향" 일반 설명 (완전 무료 - 기사마다 다르지 않고, 카테고리 단위로 미리 써둔 설명)
CATEGORY_IMPACT = {
    "전쟁·지정학": "지정학적 긴장은 보통 유가·금·안전자산 수요에 영향을 주고, 관련 지역에 노출된 기업 주가가 출렁일 수 있어요.",
    "유가·원자재": "유가·원자재 가격 변동은 항공·운송·정유 업종 실적과 소비자물가에 직접적인 영향을 줄 수 있어요.",
    "금리·연준": "금리·중앙은행 발표는 대출 비용, 주식·채권 시장 전반, 환율에 폭넓게 영향을 미치는 경향이 있어요.",
    "AI·테크": "AI·기술 관련 소식은 반도체·빅테크 주가와 관련 산업의 투자 심리에 영향을 줄 수 있어요.",
    "주식·증시": "개별 기업·시장 지표 소식은 해당 종목이나 업종의 단기 주가 흐름에 영향을 줄 수 있어요.",
    "기타": "이 카테고리는 특정 산업에 국한되지 않는 일반 경제/사회 뉴스예요.",
}

# 클러스터링(같은 사건 감지)용 불용어. 이 단어들은 핵심 단어로 세지 않습니다.
STOPWORDS = {
    "the", "a", "an", "to", "of", "in", "on", "for", "with", "as", "is",
    "are", "at", "by", "its", "after", "from", "and", "or", "new", "says",
    "say", "said", "up", "down", "over", "this", "that", "will", "be",
    "has", "have", "had", "their", "his", "her", "not", "but", "how",
    "why", "what", "who", "into", "than", "amid", "could", "would",
    "should", "more", "most", "now", "one", "two", "three", "about",
    "also", "just", "like", "them", "they", "been", "some", "here",
}

# ---------------------------------------------------------------- 번역


def translate_with_deepl(titles):
    key = os.environ.get("DEEPL_KEY", "").strip()
    if not key:
        print("[번역] DEEPL_KEY가 없어 구글 번역을 사용합니다.")
        return None

    if key.endswith(":fx"):
        endpoint = "https://api-free.deepl.com/v2/translate"
    else:
        endpoint = "https://api.deepl.com/v2/translate"

    result = {}
    for i in range(0, len(titles), 40):
        chunk = titles[i:i + 40]
        try:
            fields = [("target_lang", "KO"), ("source_lang", "EN")]
            fields += [("text", t) for t in chunk]
            body = urllib.parse.urlencode(fields).encode("utf-8")
            req = urllib.request.Request(
                endpoint,
                data=body,
                headers={
                    "Authorization": f"DeepL-Auth-Key {key}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for original, translated in zip(chunk, data["translations"]):
                text = translated.get("text", "").strip()
                if text:
                    result[original] = text
            print(f"[번역] DeepL {len(chunk)}건 처리")
        except Exception as e:
            print(f"[경고] DeepL 실패: {e}")
            return None
        time.sleep(0.5)

    return result if result else None


def translate_with_google(titles):
    SEP = "\n@@@\n"
    result = {}

    for i in range(0, len(titles), 8):
        chunk = titles[i:i + 8]
        joined = SEP.join(chunk)

        for attempt in range(3):
            try:
                params = {"client": "gtx", "sl": "en", "tl": "ko", "dt": "t", "q": joined}
                url = (
                    "https://translate.googleapis.com/translate_a/single?"
                    + urllib.parse.urlencode(params)
                )
                req = urllib.request.Request(url, headers={
                    "User-Agent": BROWSER_UA,
                    "Accept": "*/*",
                    "Accept-Language": "ko,en;q=0.9",
                })
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

                merged = "".join(seg[0] for seg in data[0] if seg and seg[0])
                parts = [p.strip() for p in merged.split("@@@")]

                if len(parts) == len(chunk):
                    for original, translated in zip(chunk, parts):
                        if translated:
                            result[original] = translated
                    print(f"[번역] 구글 {len(chunk)}건 처리")
                else:
                    print(f"[경고] 구분자 개수 불일치 {len(parts)} vs {len(chunk)}, 건너뜀")
                break
            except Exception as e:
                print(f"[번역 재시도 {attempt + 1}/3] {e}")
                time.sleep(3 * (attempt + 1))

        time.sleep(1.5)

    return result


def translate_titles(titles):
    unique = sorted(set(titles))
    if not unique:
        return {}

    mapping = translate_with_deepl(unique)
    if mapping is None:
        mapping = translate_with_google(unique)

    for t in unique:
        mapping.setdefault(t, t)
    return mapping


# ---------------------------------------------------------------- 분류·클러스터링


def classify(title):
    for category, patterns in COMPILED_RULES:
        for pattern in patterns:
            if pattern.search(title):
                return category
    return DEFAULT_CATEGORY


def significant_words(title):
    words = re.findall(r"[a-zA-Z']+", title.lower())
    return {w for w in words if len(w) > 3 and w not in STOPWORDS}


def cluster_items(items):
    clusters = []
    for item in items:
        words = significant_words(item["title"])
        matched = None

        for cluster in clusters:
            sources = {x["source"] for x in cluster["items"]}
            if item["source"] in sources:
                continue
            overlap = words & cluster["words"]
            smaller = max(1, min(len(words), len(cluster["words"])))
            if len(overlap) >= 3 and len(overlap) / smaller >= 0.5:
                matched = cluster
                break

        if matched:
            matched["items"].append(item)
            matched["words"] |= words
        else:
            clusters.append({"items": [item], "words": set(words)})

    return clusters


# ---------------------------------------------------------------- 수집


def parse_pubdate(raw):
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def clean_description(raw):
    """RSS description에서 HTML 태그를 걷어내고 적당한 길이로 자릅니다."""
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)          # 태그 제거
    text = html.unescape(text)                    # &amp; 같은 엔티티 복원
    text = re.sub(r"\s+", " ", text).strip()       # 중복 공백 정리
    if len(text) > 220:
        text = text[:220].rsplit(" ", 1)[0] + "…"
    return text


def fetch_feed(feed):
    items = []
    try:
        req = urllib.request.Request(
            feed["url"],
            headers={"User-Agent": BROWSER_UA, "Accept": "application/rss+xml, */*"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()

        root = ET.fromstring(raw)
        for node in root.findall(".//item")[:MAX_ITEMS_PER_FEED]:
            title = (node.findtext("title") or "").strip()
            link = (node.findtext("link") or "").strip()
            pub_raw = (node.findtext("pubDate") or "").strip()
            desc_raw = node.findtext("description") or node.findtext("summary") or ""

            if not title or not link:
                continue

            category = classify(title)

            # MarketWatch가 "기타"로 분류되는 기사는 영양가가 낮아 제외합니다.
            if feed["name"] == "MarketWatch" and category == DEFAULT_CATEGORY:
                continue

            items.append({
                "title": title,
                "title_ko": title,          # 나중에 번역으로 채웁니다
                "link": link,
                "published": parse_pubdate(pub_raw),
                "source": feed["name"],
                "category": category,
                "summary": clean_description(desc_raw),
                "impact": CATEGORY_IMPACT.get(category, ""),
            })

        print(f"[수집] {feed['name']}: {len(items)}건")
    except Exception as e:
        print(f"[경고] {feed['name']} 가져오기 실패: {e}")

    return items


def fetch_og_description(url):
    """
    기사 페이지에 접속해서 og:description(또는 meta description)을 가져옵니다.
    언론사가 SNS 공유용으로 직접 써둔 요약이라, RSS 소개글보다 알맹이가 있는 경우가 많아요.
    실패하면 None을 반환합니다(그러면 기존 RSS 요약을 그대로 씁니다).
    """
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": BROWSER_UA, "Accept": "text/html"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read(120_000)  # <head>만 필요하니 앞부분만 읽어서 속도를 확보
        text = raw.decode("utf-8", errors="ignore")

        patterns = [
            r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']',
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']',
        ]
        for pattern in patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                desc = html.unescape(m.group(1)).strip()
                desc = re.sub(r"\s+", " ", desc)
                if len(desc) > 220:
                    desc = desc[:220].rsplit(" ", 1)[0] + "…"
                if len(desc) > 15:  # 너무 짧으면(빈 문구 등) 의미 없다고 보고 버림
                    return desc
        return None
    except Exception as e:
        print(f"[요약 보강 실패] {url[:60]}...: {e}")
        return None


def enrich_summaries(items):
    """
    각 기사 페이지에 접속해서 더 알맹이 있는 요약으로 교체를 시도합니다.
    실패한 기사는 기존 RSS 소개글을 그대로 유지합니다(사이트가 비지 않도록).
    """
    upgraded = 0
    for item in items:
        better = fetch_og_description(item["link"])
        if better:
            item["summary"] = better
            upgraded += 1
        time.sleep(0.3)  # 언론사 서버에 너무 빠르게 연속 요청하지 않도록 살짝 대기
    print(f"[요약 보강] {upgraded} / {len(items)}건 교체")


def collect_all():
    all_items = []
    seen_links = set()

    for feed in RSS_FEEDS:
        for item in fetch_feed(feed):
            if item["link"] in seen_links:
                continue
            seen_links.add(item["link"])
            all_items.append(item)

    oldest = datetime(1970, 1, 1, tzinfo=timezone.utc)
    all_items.sort(key=lambda x: x["published"] or oldest, reverse=True)

    enrich_summaries(all_items)

    return all_items


# ---------------------------------------------------------------- 렌더링


def format_time(dt):
    if not dt:
        return ""
    return dt.astimezone(KST).strftime("%m월 %d일 %H:%M")


def render_details(item):
    """요약 + 영향 보기 (완전 무료: RSS 소개글 + 카테고리별 일반 설명)"""
    summary = html.escape(item["summary"]) if item["summary"] else "이 언론사는 별도 소개글을 제공하지 않았어요."
    impact = html.escape(item["impact"])
    return f"""
      <details class="detail">
        <summary>🔍 요약 · 영향 보기</summary>
        <p class="detail-line"><b>요약</b> {summary}</p>
        <p class="detail-line"><b>영향</b> {impact}</p>
      </details>"""


def render_item(item):
    title_ko = html.escape(item["title_ko"])
    title_en = html.escape(item["title"])
    link = html.escape(item["link"])
    source = html.escape(item["source"])
    when = html.escape(format_time(item["published"]))
    meta = f"{source} · {when}" if when else source

    original_html = ""
    if item["title_ko"] != item["title"]:
        original_html = f'\n      <p class="original">{title_en}</p>'

    return f"""
    <li class="item">
      <a class="headline" href="{link}" target="_blank" rel="noopener">{title_ko}</a>{original_html}
      <p class="meta">{meta}</p>{render_details(item)}
    </li>"""


def render_top_cluster(cluster):
    items = cluster["items"]
    main = items[0]
    title_ko = html.escape(main["title_ko"])
    title_en = html.escape(main["title"])

    links = " · ".join(
        f'<a href="{html.escape(i["link"])}" target="_blank" rel="noopener">'
        f'{html.escape(i["source"])}</a>'
        for i in items
    )

    original_html = ""
    if main["title_ko"] != main["title"]:
        original_html = f'\n      <p class="original">{title_en}</p>'

    return f"""
    <li class="item item-top">
      <a class="headline" href="{html.escape(main["link"])}" target="_blank" rel="noopener">{title_ko}</a>{original_html}
      <p class="meta">{len(items)}개 언론사 보도: {links}</p>{render_details(main)}
    </li>"""


def build_html(all_items):
    updated = datetime.now(KST).strftime("%Y년 %m월 %d일 %H:%M")

    clusters = cluster_items(all_items)
    top_clusters = sorted(
        (c for c in clusters if len(c["items"]) >= 2),
        key=lambda c: len(c["items"]),
        reverse=True,
    )
    singles = [c["items"][0] for c in clusters if len(c["items"]) == 1]

    sections = ""

    if top_clusters:
        rows = "".join(render_top_cluster(c) for c in top_clusters)
        sections += f"""
      <section class="section section-top">
        <h2>여러 곳이 함께 다룬 뉴스</h2>
        <ul class="item-list">{rows}
        </ul>
      </section>"""

    grouped = {}
    for item in singles:
        grouped.setdefault(item["category"], []).append(item)

    for category in CATEGORY_ORDER:
        items = grouped.get(category)
        if not items:
            continue
        rows = "".join(render_item(i) for i in items)
        sections += f"""
      <section class="section">
        <h2>{html.escape(category)}</h2>
        <ul class="item-list">{rows}
        </ul>
      </section>"""

    if not sections:
        sections = """
      <section class="section">
        <p class="empty">아직 불러온 기사가 없습니다. 다음 갱신은 한 시간 뒤입니다.</p>
      </section>"""

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>해외 경제뉴스 모음</title>
<style>
  :root {{
    --bg: #fbfaf7;
    --surface: #ffffff;
    --ink: #1c1b19;
    --ink-soft: #6b6862;
    --ink-faint: #9a968e;
    --rule: #e4e0d8;
    --accent: #2f5d50;
    --accent-warm: #b4552d;
  }}

  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #171917;
      --surface: #1f2220;
      --ink: #eceae5;
      --ink-soft: #a5a29b;
      --ink-faint: #7c7973;
      --rule: #32352f;
      --accent: #7fb3a1;
      --accent-warm: #dd8b5f;
    }}
  }}

  * {{ box-sizing: border-box; }}

  body {{
    margin: 0;
    padding: 0;
    background: var(--bg);
    color: var(--ink);
    font-family: "Pretendard", -apple-system, BlinkMacSystemFont,
                 "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
  }}

  .wrap {{
    max-width: 44rem;
    margin: 0 auto;
    padding: 3rem 1.25rem 5rem;
  }}

  header {{
    border-bottom: 2px solid var(--ink);
    padding-bottom: 1rem;
    margin-bottom: 2.5rem;
  }}

  h1 {{
    font-size: 1.75rem;
    font-weight: 700;
    letter-spacing: -0.02em;
    margin: 0 0 0.35rem;
  }}

  .updated {{
    color: var(--ink-soft);
    font-size: 0.8125rem;
    margin: 0;
  }}

  .section {{ margin-bottom: 2.75rem; }}

  h2 {{
    font-size: 0.9375rem;
    font-weight: 700;
    color: var(--accent);
    letter-spacing: -0.01em;
    margin: 0 0 0.875rem;
    padding-bottom: 0.5rem;
    border-bottom: 1px solid var(--rule);
  }}

  .section-top h2 {{ color: var(--accent-warm); }}

  .item-list {{
    list-style: none;
    margin: 0;
    padding: 0;
  }}

  .item {{
    background: var(--surface);
    border: 1px solid var(--rule);
    border-radius: 6px;
    padding: 1rem 1.125rem;
    margin-bottom: 0.625rem;
  }}

  .item-top {{ border-left: 3px solid var(--accent-warm); }}

  .headline {{
    display: block;
    color: var(--ink);
    font-size: 1.0625rem;
    font-weight: 600;
    line-height: 1.45;
    letter-spacing: -0.01em;
    text-decoration: none;
  }}

  .headline:hover {{ color: var(--accent); }}

  .headline:focus-visible {{
    outline: 2px solid var(--accent);
    outline-offset: 3px;
    border-radius: 2px;
  }}

  .original {{
    color: var(--ink-faint);
    font-size: 0.8125rem;
    line-height: 1.5;
    margin: 0.375rem 0 0;
  }}

  .meta {{
    color: var(--ink-soft);
    font-size: 0.75rem;
    margin: 0.5rem 0 0;
  }}

  .meta a {{
    color: var(--accent);
    font-weight: 600;
    text-decoration: none;
  }}

  .meta a:hover {{ text-decoration: underline; }}

  .detail {{
    margin-top: 0.625rem;
    border-top: 1px dashed var(--rule);
    padding-top: 0.5rem;
  }}

  .detail summary {{
    cursor: pointer;
    color: var(--accent);
    font-size: 0.8125rem;
    font-weight: 600;
  }}

  .detail-line {{
    margin: 0.5rem 0 0;
    font-size: 0.8125rem;
    color: var(--ink-soft);
    line-height: 1.55;
  }}

  .detail-line b {{
    color: var(--ink);
    margin-right: 0.25rem;
  }}

  .empty {{ color: var(--ink-soft); }}
</style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>해외 경제뉴스 모음</h1>
      <p class="updated">{updated} 기준 · 한 시간마다 갱신</p>
    </header>{sections}
  </div>
</body>
</html>
"""


# ---------------------------------------------------------------- 실행


def main():
    all_items = collect_all()
    print(f"[수집] 전체 {len(all_items)}건")

    if all_items:
        title_map = translate_titles([i["title"] for i in all_items])
        for item in all_items:
            item["title_ko"] = title_map.get(item["title"], item["title"])

        done = sum(1 for i in all_items if i["title_ko"] != i["title"])
        print(f"[번역] 제목 성공 {done} / 전체 {len(all_items)}")
        if done == 0:
            print("[경고] 번역이 하나도 되지 않았습니다. 위의 실패 메시지를 확인하세요.")

        # 요약문(summary)도 같은 방식으로 번역합니다.
        original_summaries = [i["summary"] for i in all_items if i["summary"]]
        summary_map = translate_titles(original_summaries) if original_summaries else {}
        for item in all_items:
            if item["summary"]:
                item["summary"] = summary_map.get(item["summary"], item["summary"])
        print(f"[번역] 요약 {len(original_summaries)}건 처리")

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(build_html(all_items))
    print("index.html 생성 완료")


if __name__ == "__main__":
    main()

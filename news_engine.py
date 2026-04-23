# -*- coding: utf-8 -*-
"""
News Sentiment Engine — V9.1
=================================
يجلب آخر أخبار السوق السعودي ويحلل sentiment عبر Opus.

المصادر:
  1. Argaam RSS (عربي + إنجليزي)
  2. Aleqt RSS (احتياطي)

الاستراتيجية:
  - كل 24 ساعة: جلب آخر 50-100 خبر
  - تصفية: فقط الأخبار التي تذكر رقم سهم من قائمتنا
  - تحليل دفعة واحدة عبر Opus (توفيراً للتكاليف)
  - Cache النتيجة لـ 24 ساعة

المخرج:
  {
    "2222": {"sentiment": "neutral", "score": 0.0, "headlines": [...]},
    "1120": {"sentiment": "negative", "score": -0.7, "headlines": [...]},
    ...
  }

في scanner_v9.py:
  - negative (score < -0.3) → multiplier ×0.5
  - neutral (-0.3 إلى +0.3) → multiplier 1.0
  - positive (> +0.3) → multiplier ×1.1

التكلفة: طلب Opus واحد فقط يومياً (~1000-2000 tokens)
"""
import re
import json
import time
import logging
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

log = logging.getLogger(__name__)

BASE = Path("tadawul_data")
F_NEWS_CACHE = BASE / "news_sentiment.json"
F_RAW_NEWS = BASE / "news_raw.json"
CACHE_HOURS = 20  # تحديث كل 20 ساعة (أقل من 24 لضمان تحديث يومي)

ARGAAM_RSS_URLS = [
    "https://www.argaam.com/ar/rss/saudi",  # عربي - السعودي
    "https://www.argaam.com/ar/rss/tasi",   # عربي - تاسي
    "https://www.argaam.com/en/rss/saudi",  # إنجليزي
]

USER_AGENT = "Mozilla/5.0 (compatible; TadawulV9/1.0)"


def _fetch_url(url, timeout=15):
    """جلب URL بسيط مع timeout."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="ignore")
    except Exception as e:
        log.debug(f"fetch {url}: {e}")
        return None


def _parse_rss(xml_text):
    """يستخرج (title, description, link, pubDate) من RSS XML."""
    items = []
    if not xml_text:
        return items
    try:
        root = ET.fromstring(xml_text)
        # RSS 2.0 standard
        for item in root.iter("item"):
            title = item.findtext("title", "").strip()
            desc = item.findtext("description", "").strip()
            link = item.findtext("link", "").strip()
            date = item.findtext("pubDate", "").strip()
            # remove HTML tags from description
            desc = re.sub(r"<[^>]+>", "", desc)
            desc = re.sub(r"\s+", " ", desc).strip()
            if title:
                items.append({
                    "title": title,
                    "description": desc[:300],
                    "link": link,
                    "date": date,
                })
    except Exception as e:
        log.debug(f"parse RSS: {e}")
    return items


def _extract_ticker_mentions(text, known_tickers):
    """
    يستخرج أرقام الأسهم المذكورة في نص.
    نبحث عن أرقام 4 خانات (1xxx-8xxx) تنتمي لقائمتنا.
    """
    mentions = set()
    # أنماط شائعة في الأخبار السعودية:
    # "الراجحي (1120)" أو "2222" أو "SABIC (2010)"
    patterns = [
        r"\b(\d{4})\b",  # أي رقم 4 خانات
    ]
    for p in patterns:
        for m in re.finditer(p, text):
            code = m.group(1)
            if code in known_tickers:
                mentions.add(code)
    return mentions


def fetch_recent_news(known_tickers, max_items=80):
    """
    يجلب آخر أخبار من Argaam ويُصفّيها.
    known_tickers: set of ticker codes (e.g., {"2222", "1120", ...})
    
    Returns:
        list of {"title", "description", "link", "date", "tickers": [codes]}
    """
    all_items = []
    
    for url in ARGAAM_RSS_URLS:
        log.info(f"Fetching: {url}")
        xml = _fetch_url(url)
        if not xml:
            continue
        items = _parse_rss(xml)
        log.info(f"  → {len(items)} خبر")
        all_items.extend(items)
        time.sleep(0.5)
    
    # إزالة التكرارات (بنفس العنوان)
    seen_titles = set()
    unique = []
    for item in all_items:
        title_key = item["title"][:100]
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        
        # استخراج الأسهم المذكورة
        full_text = f"{item['title']} {item['description']}"
        tickers = _extract_ticker_mentions(full_text, known_tickers)
        if tickers:
            item["tickers"] = sorted(tickers)
            unique.append(item)
    
    # آخر max_items خبر
    unique = unique[:max_items]
    log.info(f"أخبار ذات صلة: {len(unique)}")
    
    # حفظ الخام للرجوع
    try:
        F_RAW_NEWS.parent.mkdir(parents=True, exist_ok=True)
        with open(F_RAW_NEWS, "w", encoding="utf-8") as f:
            json.dump({
                "fetched_at": datetime.now().isoformat(),
                "count": len(unique),
                "items": unique,
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"save raw news: {e}")
    
    return unique


def _build_sentiment_prompt(news_items):
    """يبني prompt لـ Opus لتحليل sentiment دفعة واحدة."""
    ticker_news = {}
    for item in news_items:
        for t in item.get("tickers", []):
            ticker_news.setdefault(t, []).append(item)
    
    if not ticker_news:
        return None, {}
    
    # نحد كل سهم بآخر 3 أخبار (لتقليل tokens)
    prompt_parts = []
    for ticker, items in ticker_news.items():
        prompt_parts.append(f"\n=== {ticker} ===")
        for i, item in enumerate(items[:3], 1):
            prompt_parts.append(f"{i}. {item['title']}")
            if item.get("description"):
                prompt_parts.append(f"   {item['description'][:200]}")
    
    news_block = "\n".join(prompt_parts)
    
    system = """أنت محلل سعودي خبير. مهمتك تصنيف sentiment أخبار الأسهم السعودية.

لكل سهم، أعطِ:
- sentiment: "positive" | "negative" | "neutral"
- score: عدد بين -1.0 و +1.0 حيث:
  * +1.0 = خبر ممتاز (أرباح قوية، عقد ضخم، ترقية)
  * +0.3 إلى +0.7 = خبر إيجابي معتدل
  * -0.3 إلى +0.3 = محايد (إعلان روتيني، بيانات عامة)
  * -0.3 إلى -0.7 = خبر سلبي (خسائر، تراجع، تحذيرات)
  * -1.0 = خبر سيء جداً (احتيال، تعليق، عقوبات)
- reason: سبب مركّز (20 كلمة حد أقصى)

رد بـ JSON فقط (بدون backticks):
{"2222": {"sentiment": "positive", "score": 0.6, "reason": "..."}, ...}"""

    user = f"حلل هذه الأخبار من Argaam:\n{news_block}"
    
    return (system, user), ticker_news


def analyze_sentiment_with_opus(news_items, api_key=None):
    """يحلل sentiment عبر Opus. يرجع dict {ticker: {sentiment, score, reason}}."""
    import os
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        log.warning("No API key — skipping sentiment analysis")
        return {}
    
    prompt_data, ticker_news = _build_sentiment_prompt(news_items)
    if not prompt_data:
        return {}
    
    system, user = prompt_data
    
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=2500,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        rt = msg.content[0].text.strip()
        # clean backticks
        if rt.startswith("```"):
            rt = rt.split("\n", 1)[1]
        if rt.endswith("```"):
            rt = rt.rsplit("```", 1)[0]
        rt = rt.strip()
        
        result = json.loads(rt)
        
        # أضف metadata
        for ticker, info in result.items():
            items = ticker_news.get(ticker, [])
            info["headlines"] = [it["title"] for it in items[:3]]
            info["news_count"] = len(items)
        
        log.info(f"Sentiment analyzed for {len(result)} tickers, "
                 f"cost: ~${(msg.usage.input_tokens*5 + msg.usage.output_tokens*25)/1_000_000:.4f}")
        return result
    except Exception as e:
        log.error(f"Opus sentiment: {e}")
        return {}


def is_cache_fresh():
    """هل الكاش ما زال صالحاً (< CACHE_HOURS)؟"""
    if not F_NEWS_CACHE.exists():
        return False
    try:
        with open(F_NEWS_CACHE, encoding="utf-8") as f:
            data = json.load(f)
        fetched = datetime.fromisoformat(data.get("fetched_at", ""))
        age = datetime.now() - fetched
        return age < timedelta(hours=CACHE_HOURS)
    except Exception:
        return False


def get_sentiment_for_all(known_tickers, force_refresh=False):
    """
    الدالة الرئيسية: يرجع sentiment لكل الأسهم مع cache.
    
    Returns: {
        "fetched_at": "...",
        "sentiments": {
            "2222": {"sentiment": "positive", "score": 0.5, "headlines": [...]},
            ...
        }
    }
    """
    # تحقق من الكاش
    if not force_refresh and is_cache_fresh():
        try:
            with open(F_NEWS_CACHE, encoding="utf-8") as f:
                cached = json.load(f)
            log.info(f"Using cached sentiment ({len(cached.get('sentiments', {}))} tickers)")
            return cached
        except Exception as e:
            log.debug(f"cache read: {e}")
    
    # جلب أخبار جديدة
    log.info("Fetching fresh news...")
    news = fetch_recent_news(known_tickers, max_items=80)
    
    if not news:
        log.warning("No news fetched — returning empty sentiment")
        result = {
            "fetched_at": datetime.now().isoformat(),
            "sentiments": {},
            "news_count": 0,
        }
    else:
        sentiments = analyze_sentiment_with_opus(news)
        result = {
            "fetched_at": datetime.now().isoformat(),
            "sentiments": sentiments,
            "news_count": len(news),
        }
    
    # حفظ الكاش
    try:
        F_NEWS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with open(F_NEWS_CACHE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"save cache: {e}")
    
    return result


def get_ticker_multiplier(ticker, sentiment_data):
    """
    يرجع multiplier للسهم بناءً على sentiment.
    - score < -0.5: ×0.4 (خبر سيء جداً، تجنب)
    - score < -0.3: ×0.6 (خبر سلبي، حذر)
    - score < 0.3: ×1.0 (محايد، لا تأثير)
    - score < 0.5: ×1.08 (إيجابي خفيف)
    - score >= 0.5: ×1.15 (إيجابي قوي)
    
    Returns: (multiplier, sentiment_label)
    """
    sentiments = sentiment_data.get("sentiments", {})
    info = sentiments.get(ticker)
    if not info:
        return 1.0, "no_news"
    
    score = info.get("score", 0)
    if score < -0.5:
        return 0.4, "very_negative"
    elif score < -0.3:
        return 0.6, "negative"
    elif score < 0.3:
        return 1.0, "neutral"
    elif score < 0.5:
        return 1.08, "positive"
    else:
        return 1.15, "very_positive"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # اختبار سريع
    test_tickers = {"2222", "1120", "2010", "4190", "5110"}
    result = get_sentiment_for_all(test_tickers, force_refresh=True)
    print(f"\nFetched at: {result.get('fetched_at')}")
    print(f"News count: {result.get('news_count')}")
    print(f"Sentiments:")
    for t, info in result.get("sentiments", {}).items():
        print(f"  {t}: {info.get('sentiment')} ({info.get('score')}) — {info.get('reason', '')}")

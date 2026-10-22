#!/usr/bin/env python3
"""Amazon.eg price-drop watcher -> Telegram alert.

asins.txt lines can be: a product link (/dp/...), a bare ASIN, or a category/search link (watches every item on it).
  python watcher.py          # run forever
  python watcher.py --once   # one pass (GitHub Actions)
  python watcher.py --test   # offline self-check
"""
import os, re, sqlite3, sys, time, random
import requests
from bs4 import BeautifulSoup

DROP_PCT = float(os.environ.get("DROP_PCT", "40"))      # alert when price is this % below normal
INTERVAL_MIN = float(os.environ.get("INTERVAL_MIN", "10"))
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
HERE = os.path.dirname(os.path.abspath(__file__))
ASIN_IN_URL = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})")


def load_targets(path):
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.split("#")[0].strip()
        m = ASIN_IN_URL.search(line) or re.fullmatch(r"([A-Z0-9]{10})", line)
        if m:
            out.append(f"https://www.amazon.eg/dp/{m.group(1)}?language=en_AE")
        elif line.startswith("http"):
            out.append(line + ("&" if "?" in line else "?") + "language=en_AE")
    return out


def to_num(text):
    digits = re.sub(r"[^\d.]", "", text.replace(",", ""))
    return float(digits) if digits else None


def parse(html):
    """Product page -> {title, price, amazon_pct}."""
    s = BeautifulSoup(html, "html.parser")
    title = s.select_one("#productTitle")
    price = s.select_one("#corePrice_feature_div .a-offscreen, "
                         "#corePriceDisplay_desktop_feature_div .a-offscreen, "
                         "#apex_desktop .a-price .a-offscreen")
    pct = s.select_one(".savingsPercentage")
    return {
        "title": title.get_text(strip=True) if title else "?",
        "price": to_num(price.get_text()) if price else None,
        "amazon_pct": abs(to_num(pct.get_text()) or 0) if pct else 0,  # Amazon's own "-91%" badge
    }


def parse_list(html):
    """Category/search page -> [(asin, info)] for every item showing a price."""
    s = BeautifulSoup(html, "html.parser")
    out = []
    for d in s.select('div[data-component-type="s-search-result"][data-asin]'):
        price = d.select_one(".a-price:not(.a-text-price) .a-offscreen")
        was = d.select_one(".a-price.a-text-price .a-offscreen")
        p = to_num(price.get_text()) if price else None
        w = to_num(was.get_text()) if was else None
        if d["data-asin"] and p:
            title = d.select_one("h2")
            out.append((d["data-asin"], {
                "title": title.get_text(" ", strip=True) if title else "?",
                "price": p,
                "amazon_pct": (1 - p / w) * 100 if w and w > p else 0,
            }))
    return out


def should_alert(price, avg, last, amazon_pct, drop=DROP_PCT):
    """Return reason string or None. Alerts once when the price crosses the threshold, not every cycle."""
    if avg:
        limit = avg * (1 - drop / 100)
        if price <= limit and (last is None or last > limit):
            return f"أقل من المتوسط بـ {100 - price / avg * 100:.0f}%"
    if amazon_pct >= drop and (last is None or price < last):
        return f"خصم أمازون {amazon_pct:.0f}%"
    return None


def notify(text):
    requests.post(f"https://api.telegram.org/bot{os.environ['TG_BOT_TOKEN']}/sendMessage",
                  data={"chat_id": os.environ["TG_CHAT_ID"], "text": text}, timeout=15)


def record(db, asin, info):
    avg, = db.execute("SELECT AVG(price) FROM prices WHERE asin=? AND ts > ?",
                      (asin, time.time() - 30 * 86400)).fetchone()
    row = db.execute("SELECT price FROM prices WHERE asin=? ORDER BY ts DESC LIMIT 1", (asin,)).fetchone()
    reason = should_alert(info["price"], avg, row[0] if row else None, info["amazon_pct"])
    db.execute("INSERT INTO prices VALUES (?,?,?)", (asin, info["price"], time.time()))
    if reason:
        print(f"ALERT {asin}: {info['price']:,.0f} {reason}")
        notify(f"🔥 {reason}\n{info['title'][:120]}\nالسعر: {info['price']:,.0f} ج\n"
               f"https://www.amazon.eg/dp/{asin}")


def check(db, url):
    r = requests.get(url, headers=HEADERS, timeout=20)
    if r.status_code != 200 or "validateCaptcha" in r.text:
        print(f"{url}: blocked/captcha ({r.status_code}), skipping")
        return False
    m = ASIN_IN_URL.search(url)
    items = [(m.group(1), parse(r.text))] if m else parse_list(r.text)
    items = [(a, i) for a, i in items if i["price"]]
    print(f"{url[:90]}: {len(items)} priced items")
    for asin, info in items:
        record(db, asin, info)
    db.commit()
    return True


def main(once=False):
    db = sqlite3.connect(os.path.join(HERE, "prices.db"))
    db.execute("CREATE TABLE IF NOT EXISTS prices (asin TEXT, price REAL, ts REAL)")
    while True:
        blocked = 0
        for url in load_targets(os.path.join(HERE, "asins.txt")):
            try:
                if not check(db, url):
                    blocked += 1
            except Exception as e:  # network hiccup: keep the loop alive
                print(f"{url[:90]}: error {e}")
            time.sleep(random.uniform(4, 9))  # be gentle, avoids getting blocked
        db.execute("DELETE FROM prices WHERE ts < ?", (time.time() - 30 * 86400,))  # keep the cache small
        db.commit()
        if once:  # GitHub Actions: one pass per scheduled run
            return
        wait = INTERVAL_MIN * 60 * (3 if blocked else 1)  # back off if Amazon starts blocking
        print(f"--- sleeping {wait / 60:.0f} min ---")
        time.sleep(wait)


def selftest():
    html = """<span id="productTitle"> LG Split AC 2.25HP </span>
    <div id="corePriceDisplay_desktop_feature_div"><span class="savingsPercentage">-91%</span>
    <span class="a-price"><span class="a-offscreen">EGP4,000.00</span></span></div>"""
    assert parse(html) == {"title": "LG Split AC 2.25HP", "price": 4000.0, "amazon_pct": 91.0}, parse(html)
    listing = """
    <div data-component-type="s-search-result" data-asin="B0AAAAAAAA"><h2><span>LG AC</span></h2>
      <span class="a-price"><span class="a-offscreen">EGP4,000.00</span></span>
      <span class="a-price a-text-price"><span class="a-offscreen">EGP40,000.00</span></span></div>
    <div data-component-type="s-search-result" data-asin="B0BBBBBBBB"><h2>Fridge</h2>
      <span class="a-price"><span class="a-offscreen">EGP30,000.00</span></span></div>
    <div data-component-type="s-search-result" data-asin=""><h2>ad</h2></div>"""
    assert parse_list(listing) == [
        ("B0AAAAAAAA", {"title": "LG AC", "price": 4000.0, "amazon_pct": 90.0}),
        ("B0BBBBBBBB", {"title": "Fridge", "price": 30000.0, "amazon_pct": 0}),
    ], parse_list(listing)
    assert should_alert(4000, 46000, 46000, 0, 40)            # big drop vs history
    assert not should_alert(4000, 46000, 4000, 0, 40)         # already alerted last cycle
    assert not should_alert(44000, 46000, 46000, 0, 40)       # normal fluctuation
    assert should_alert(4000, None, None, 91, 40)             # first sight, Amazon shows -91%
    open(os.path.join(HERE, "_t.txt"), "w").write(
        "# c\nhttps://www.amazon.eg/-/en/x/dp/B0CXYZ1234?th=1\nB0DDDDDDDD\nhttps://www.amazon.eg/s?rh=n%3A1\n")
    assert load_targets(os.path.join(HERE, "_t.txt")) == [
        "https://www.amazon.eg/dp/B0CXYZ1234?language=en_AE",
        "https://www.amazon.eg/dp/B0DDDDDDDD?language=en_AE",
        "https://www.amazon.eg/s?rh=n%3A1&language=en_AE"]
    os.remove(os.path.join(HERE, "_t.txt"))
    print("self-test OK")


if __name__ == "__main__":
    selftest() if "--test" in sys.argv else main(once="--once" in sys.argv)

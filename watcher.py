#!/usr/bin/env python3
"""Amazon.eg price-drop watcher -> Telegram alert.

Usage:
  set TG_BOT_TOKEN and TG_CHAT_ID env vars, put ASINs or product links in asins.txt, then:
  python watcher.py          # run forever
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


def load_asins(path):
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.split("#")[0].strip()
        m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", line) or re.fullmatch(r"([A-Z0-9]{10})", line)
        if m:
            out.append(m.group(1))
    return out


def to_num(text):
    digits = re.sub(r"[^\d.]", "", text.replace(",", ""))
    return float(digits) if digits else None


def parse(html):
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


def check(db, asin):
    url = f"https://www.amazon.eg/dp/{asin}?language=en_AE"
    r = requests.get(url, headers=HEADERS, timeout=20)
    if r.status_code != 200 or "validateCaptcha" in r.text:
        print(f"{asin}: blocked/captcha ({r.status_code}), skipping")
        return False
    info = parse(r.text)
    if info["price"] is None:
        print(f"{asin}: no price (out of stock?)")
        return True
    avg, = db.execute("SELECT AVG(price) FROM prices WHERE asin=? AND ts > ?",
                      (asin, time.time() - 30 * 86400)).fetchone()
    row = db.execute("SELECT price FROM prices WHERE asin=? ORDER BY ts DESC LIMIT 1", (asin,)).fetchone()
    last = row[0] if row else None
    reason = should_alert(info["price"], avg, last, info["amazon_pct"])
    db.execute("INSERT INTO prices VALUES (?,?,?)", (asin, info["price"], time.time()))
    db.commit()
    print(f"{asin}: {info['price']:,.0f} EGP (avg {avg or 0:,.0f}) {reason or ''}")
    if reason:
        notify(f"🔥 {reason}\n{info['title'][:120]}\nالسعر: {info['price']:,.0f} ج\n{url}")
    return True


def main(once=False):
    db = sqlite3.connect(os.path.join(HERE, "prices.db"))
    db.execute("CREATE TABLE IF NOT EXISTS prices (asin TEXT, price REAL, ts REAL)")
    while True:
        blocked = 0
        for asin in load_asins(os.path.join(HERE, "asins.txt")):
            try:
                if not check(db, asin):
                    blocked += 1
            except Exception as e:  # network hiccup: keep the loop alive
                print(f"{asin}: error {e}")
            time.sleep(random.uniform(4, 9))  # be gentle, avoids getting blocked
        if once:  # GitHub Actions: one pass per scheduled run
            return
        wait = INTERVAL_MIN * 60 * (3 if blocked else 1)  # back off if Amazon starts blocking
        print(f"--- sleeping {wait / 60:.0f} min ---")
        time.sleep(wait)


def selftest():
    html = """<span id="productTitle"> LG Split AC 2.25HP </span>
    <div id="corePriceDisplay_desktop_feature_div"><span class="savingsPercentage">-91%</span>
    <span class="a-price"><span class="a-offscreen">EGP4,000.00</span></span></div>"""
    p = parse(html)
    assert p == {"title": "LG Split AC 2.25HP", "price": 4000.0, "amazon_pct": 91.0}, p
    assert should_alert(4000, 46000, 46000, 0, 40)            # big drop vs history
    assert not should_alert(4000, 46000, 4000, 0, 40)         # already alerted last cycle
    assert not should_alert(44000, 46000, 46000, 0, 40)       # normal fluctuation
    assert should_alert(4000, None, None, 91, 40)             # first sight, Amazon shows -91%
    assert load_asins.__name__ and re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})",
                                             "https://www.amazon.eg/-/en/x/dp/B0CXYZ1234?th=1").group(1) == "B0CXYZ1234"
    print("self-test OK")


if __name__ == "__main__":
    selftest() if "--test" in sys.argv else main(once="--once" in sys.argv)

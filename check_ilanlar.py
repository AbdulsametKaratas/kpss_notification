#!/usr/bin/env python3
"""Kariyer Kapısı RSS feed'lerini kontrol eder, yeni ilanları ntfy ile bildirir."""
import json
import os
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
SEEN_PATH = ROOT / "seen.json"

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_TR = str.maketrans("ığüşöç", "igusoc")


def norm(s):
    s = (s or "").replace("İ", "i").replace("I", "ı").lower()
    return s.translate(_TR)


def fetch(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def parse_date(s):
    try:
        return parsedate_to_datetime(s)
    except Exception:  # noqa: BLE001
        return None


def parse_items(data):
    root = ET.fromstring(data)  # bytes ver: BOM'u expat halleder
    items = []
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        guid = (it.findtext("guid") or link).strip()
        if not guid:
            continue
        dt = parse_date((it.findtext("pubDate") or "").strip())
        items.append(
            {
                "id": guid,
                "link": link,
                "title": (it.findtext("title") or "Yeni ilan").strip(),
                "category": (it.findtext("category") or "").strip(),
                "dt": dt,
                "date": dt.strftime("%d.%m.%Y %H:%M") if dt else "",
            }
        )
    return items


def category_ok(item):
    wanted = [norm(k) for k in CONFIG.get("kategoriler", [])]
    return not wanted or any(k in norm(item["category"]) for k in wanted)


def calendar_url(item):
    """Tek dokunuşla Google Takvim'e ekleme linki (OAuth gerektirmez)."""
    dt = item.get("dt")
    if not dt:
        return None
    start = dt.astimezone(timezone.utc)
    end = start + timedelta(hours=1)
    f = "%Y%m%dT%H%M%SZ"
    params = {
        "action": "TEMPLATE",
        "text": f"Kariyer Kapısı: {item['title']}"[:200],
        "dates": f"{start.strftime(f)}/{end.strftime(f)}",
        "details": f"{item['category']}\n{item['link']}".strip(),
    }
    return "https://calendar.google.com/calendar/render?" + urllib.parse.urlencode(params)


def notify(item):
    message = "\n".join(
        x for x in [item["category"], f"Tarih: {item['date']}" if item["date"] else ""] if x
    )
    cal = calendar_url(item)
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print(f"[DRY-RUN] {item['title']}\n  {message}\n  {item['link']}\n  takvim: {cal}")
        return
    payload = {
        "topic": topic,
        "title": item["title"][:120],
        "message": message or item["title"],
        "click": item["link"],
        "tags": ["bell"],
    }
    if cal:
        payload["actions"] = [{"action": "view", "label": "Takvime ekle", "url": cal}]
    req = urllib.request.Request(
        "https://ntfy.sh/",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=20).read()


def main():
    seen = set(json.loads(SEEN_PATH.read_text())) if SEEN_PATH.exists() else set()
    feeds = CONFIG["feeds"]

    items, feed_errors = {}, 0
    for url in feeds:
        try:
            for it in parse_items(fetch(url)):
                items[it["id"]] = it
        except Exception as e:  # noqa: BLE001
            feed_errors += 1
            print(f"HATA feed alınamadı {url}: {e}", file=sys.stderr)

    new = [it for it in items.values() if it["id"] not in seen and category_ok(it)]
    print(f"{len(items)} ilan feed'de, {len(new)} yeni")

    notify_errors = 0
    for it in new[: CONFIG.get("max_bildirim_per_run", 10)]:
        try:
            notify(it)
            seen.add(it["id"])
        except Exception as e:  # noqa: BLE001
            notify_errors += 1
            print(f"HATA bildirim gitmedi ({it['title']}): {e}", file=sys.stderr)

    SEEN_PATH.write_text(
        json.dumps(sorted(seen), ensure_ascii=False, indent=1), encoding="utf-8"
    )
    # Tüm feed'ler patlarsa ya da bildirim gitmezse Action kırmızı olsun, sessizce kaçırmayalım
    if feed_errors == len(feeds) or notify_errors:
        sys.exit(1)


if __name__ == "__main__":
    main()

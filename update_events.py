# -*- coding: utf-8 -*-
"""こぎの岩手カレンダー（公開されている予定表）を読み込み、
ページ用のデータ events.js を作り直すプログラム。

使い方:  python3 update_events.py
"""
import datetime as dt
import html
import json
import os
import re
import urllib.request

ICS_URL = "https://calendar.google.com/calendar/ical/cogi.kaicho%40gmail.com/public/basic.ics"
DAYS_AHEAD = 62          # 今日から何日先まで載せるか
LONG_DAYS = 7            # これより長い催しは「開催中」の欄にまとめる
HERE = os.path.dirname(os.path.abspath(__file__))

# カレンダーの市町村別カラー
C_RED, C_FLAMINGO, C_BLUE, C_YELLOW = "#D50000", "#E67C73", "#039BE5", "#F6BF26"
C_GREEN, C_ORANGE, C_GRAY, C_PURPLE, C_INDIGO = "#0B8043", "#F4511E", "#616161", "#8E24AA", "#3F51B5"
COLORS = {"盛岡市": C_RED, "滝沢市": C_GREEN, "遠野市": C_GRAY, "紫波町": C_PURPLE}
for m in ["花巻市", "北上市", "金ケ崎町", "西和賀町"]:
    COLORS[m] = C_FLAMINGO
for m in ["洋野町", "久慈市", "野田村", "普代村", "田野畑村", "岩泉町", "宮古市", "山田町",
          "大槌町", "釜石市", "大船渡市", "陸前高田市"]:
    COLORS[m] = C_BLUE
for m in ["矢巾町", "二戸市", "一戸町", "軽米町", "九戸村"]:
    COLORS[m] = C_YELLOW
for m in ["一関市", "平泉町"]:
    COLORS[m] = C_ORANGE
for m in ["奥州市", "雫石町", "八幡平市", "住田町", "岩手町", "葛巻町"]:
    COLORS[m] = C_INDIGO
DEFAULT_COLOR = "#6B4F4F"
ALIASES = {"金ヶ崎町": "金ケ崎町", "陸前高田": "陸前高田市"}

WD = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
JST = dt.timezone(dt.timedelta(hours=9))
URL_RE = re.compile(r"https?://[^\s<>\"'　）】」]+")


def unesc(s):
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            n = s[i + 1]
            out.append("\n" if n in "nN" else n)
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def to_text(desc):
    """説明文のHTMLをふつうの文章に直す。"""
    def link(m):
        href, text = html.unescape(m.group(1)), re.sub(r"<[^>]+>", "", m.group(2))
        return text if "http" in text else "%s %s" % (text, href)
    s = re.sub(r'<a\s[^>]*?href="([^"]*)"[^>]*>(.*?)</a>', link, desc, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s).replace("\xa0", " ")
    return "\n".join(l.strip() for l in s.split("\n"))


def parse_date(params, val):
    """(日付, 時刻の文字 or '') を返す。"""
    if "VALUE=DATE" in params or len(val) == 8:
        return dt.date(int(val[:4]), int(val[4:6]), int(val[6:8])), ""
    d = dt.datetime.strptime(val[:15], "%Y%m%dT%H%M%S")
    if val.endswith("Z"):
        d = d.replace(tzinfo=dt.timezone.utc).astimezone(JST)
    return d.date(), d.strftime("%H:%M")


def read_events(raw):
    raw = raw.replace("\r\n", "\n")
    raw = re.sub(r"\n[ \t]", "", raw)
    events = []
    for block in raw.split("BEGIN:VEVENT")[1:]:
        ev = {"exdates": set()}
        for line in block.split("END:VEVENT")[0].split("\n"):
            m = re.match(r"([A-Z\-]+)((?:;[^:]*)?):(.*)", line)
            if not m:
                continue
            key, params, val = m.groups()
            if key == "DTSTART":
                ev["start"], ev["time"] = parse_date(params, val)
                ev["allday"] = ev["time"] == ""
            elif key == "DTEND":
                ev["end"], _ = parse_date(params, val)
            elif key == "EXDATE":
                for v in val.split(","):
                    ev["exdates"].add(parse_date(params, v)[0])
            elif key == "RECURRENCE-ID":
                ev["recid"] = parse_date(params, val)[0]
            elif key in ("SUMMARY", "DESCRIPTION", "LOCATION"):
                ev[key.lower()] = unesc(val)
            elif key in ("UID", "RRULE", "STATUS"):
                ev[key.lower()] = val
        if "start" in ev and ev.get("status") != "CANCELLED":
            events.append(ev)
    return events


def nth_weekday(year, month, wd, n):
    if n > 0:
        d = dt.date(year, month, 1)
        d += dt.timedelta(days=(wd - d.weekday()) % 7 + 7 * (n - 1))
    else:
        nxt = dt.date(year + (month == 12), month % 12 + 1, 1)
        d = nxt - dt.timedelta(days=1)
        d -= dt.timedelta(days=(d.weekday() - wd) % 7 + 7 * (-n - 1))
    return d if d.month == month else None


def occurrences(ev, lo, hi):
    """くり返しの予定を、lo〜hi の範囲の開始日に広げる。"""
    start = ev["start"]
    if "rrule" not in ev:
        return [start]
    r = dict(p.split("=", 1) for p in ev["rrule"].split(";") if "=" in p)
    freq, interval = r.get("FREQ"), int(r.get("INTERVAL", 1))
    until = parse_date("", r["UNTIL"])[0] if "UNTIL" in r else hi
    count = int(r["COUNT"]) if "COUNT" in r else None
    last = min(until, hi)
    byday = r.get("BYDAY", "").split(",") if r.get("BYDAY") else []
    out, d = [], start
    if freq == "DAILY":
        while d <= last:
            out.append(d)
            d += dt.timedelta(days=interval)
    elif freq == "WEEKLY":
        days = sorted(WD[b[-2:]] for b in byday) or [start.weekday()]
        week = start - dt.timedelta(days=start.weekday())
        while week <= last:
            for w in days:
                d = week + dt.timedelta(days=w)
                if start <= d <= last:
                    out.append(d)
            week += dt.timedelta(weeks=interval)
    elif freq == "MONTHLY":
        y, mth = start.year, start.month
        while dt.date(y, mth, 1) <= last:
            cands = []
            if byday:
                for b in byday:
                    n = int(b[:-2]) if b[:-2] else 1
                    cands.append(nth_weekday(y, mth, WD[b[-2:]], n))
            else:
                day = int(r.get("BYMONTHDAY", start.day))
                try:
                    cands.append(dt.date(y, mth, day))
                except ValueError:
                    pass
            out += [c for c in cands if c and start <= c <= last]
            mth += interval
            y, mth = y + (mth - 1) // 12, (mth - 1) % 12 + 1
    elif freq == "YEARLY":
        y = start.year
        while y <= last.year:
            try:
                d = start.replace(year=y)
                if d <= last:
                    out.append(d)
            except ValueError:
                pass
            y += interval
    else:
        out = [start]
    out.sort()
    if count:
        out = out[:count]
    return [d for d in out if d >= lo - dt.timedelta(days=60)]


def parse_fields(text, summary):
    """【日時】【場所】【公式ページ】… を項目ごとに分ける。"""
    head = re.search(r"【.+?】", summary)
    fields, cur, first = [], None, True
    for line in text.split("\n"):
        if not line:
            continue
        if first:
            first = False
            same = line.replace(" ", "") == summary.replace(" ", "")
            if same or (head and head.group(0) in line):
                continue  # 1行目はタイトルのくり返し
        m = re.match(r"【([^】]{1,30})】\s*(.*)", line)
        if m:
            cur = {"k": m.group(1), "lines": [m.group(2)]}
            fields.append(cur)
        elif cur is None:
            cur = {"k": "", "lines": [line]}
            fields.append(cur)
        else:
            cur["lines"].append(line)
    out = []
    for f in fields:
        texts, links = [], []
        for line in f["lines"]:
            urls = [u.rstrip("。、.,") for u in URL_RE.findall(line)]
            rest = URL_RE.sub("", line).strip()
            if not urls:
                if rest and not rest.startswith(("#", "＃")):
                    texts.append(rest)
                continue
            label = rest.strip("（）()：:・　 ")
            if len(label) > 14:
                texts.append(rest)
                label = ""
            for u in urls:
                if u not in [x["url"] for x in links]:
                    links.append({"url": u, "label": label})
        if texts or links:
            out.append({"k": f["k"], "t": "\n".join(texts), "u": links})
    return out


def build(raw, today):
    lo, hi = today, today + dt.timedelta(days=DAYS_AHEAD)
    events = read_events(raw)
    overrides = {}
    for ev in events:
        if "recid" in ev:
            overrides.setdefault(ev.get("uid"), set()).add(ev["recid"])
    items = []
    for ev in events:
        length = max((ev.get("end", ev["start"]) - ev["start"]).days, 0)
        if ev["allday"]:
            length = max(length - 1, 0)   # 終日の予定は終了日が「翌日」になっている
        skip = set(ev["exdates"])
        if "rrule" in ev:
            skip |= overrides.get(ev.get("uid"), set())
        summary = ev.get("summary", "").strip()
        if not summary:
            continue
        m = re.match(r"(.*?)【(.+?)】\s*(.*)", summary)
        tag, muni, name = (m.group(1).strip(), m.group(2).strip(), m.group(3).strip()) if m else ("", "", summary)
        muni = ALIASES.get(muni, muni)
        fields = parse_fields(to_text(ev.get("description", "")), summary)
        loc = ev.get("location", "").strip()
        if loc and not any(f["k"] in ("場所", "会場") for f in fields):
            fields.insert(0, {"k": "場所", "t": loc, "u": []})
        for s in occurrences(ev, lo, hi):
            e = s + dt.timedelta(days=length)
            if s in skip or e < lo or s > hi:
                continue
            items.append({
                "s": s.isoformat(), "e": e.isoformat(), "time": ev["time"],
                "tag": tag, "muni": muni, "name": name,
                "color": COLORS.get(muni, DEFAULT_COLOR),
                "long": length >= LONG_DAYS, "f": fields,
            })
    items.sort(key=lambda x: (x["s"], x["muni"], x["name"]))
    return items


def main():
    now = dt.datetime.now(JST)
    req = urllib.request.Request(ICS_URL, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=60).read().decode("utf-8")
    items = build(raw, now.date())
    data = {"updated": now.strftime("%Y-%m-%d %H:%M"), "events": items}
    with open(os.path.join(HERE, "events.js"), "w", encoding="utf-8") as f:
        f.write("window.IWATE_EVENTS = ")
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    print("書き出しました: %d 件（%s 時点）" % (len(items), data["updated"]))


if __name__ == "__main__":
    main()

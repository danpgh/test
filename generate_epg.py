#!/usr/bin/env python3
import gzip
import io
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "epg.json"

XMLTV_SOURCES = [
    "https://iptv-epg.org/files/epg-ro.xml",
    "https://raw.githubusercontent.com/globetvapp/epg/main/Romania/romania1.xml",
    "https://raw.githubusercontent.com/globetvapp/epg/main/Romania/romania2.xml",
]

CHANNELS = [
    "PROTV HD","A1 HD","TVR1","TVR2","CANAL D","CANAL D2","PRIMA TV","NATIONAL TV","TVR 3","TVR INTERNATIONAL",
    "PROCINEMA","ACASA TV","ACASA GOLD","ANTENA STARS","NATIONAL 24 PLUS","A3 CNN","ROMANIA TV","REALITATEA TV","PRIMA NEWS",
    "B1 TV","EURONEWS ROMANIA","NEWS24","CNN","BBC NEWS","CBS REALITY","SUPERSPORT 1","SUPERSPORT 2","SUPERSPORT 3",
    "SUPERSPORT 4","PRO ARENA","TVR SPORT","PRIMA SPORT 1","PRIMA SPORT 2","PRIMA SPORT 3","PRIMA SPORT 4","PRIMA SPORT 5",
    "PPV 1","PPV 2","PPV 3","SPORT EXTRA","EUR0SP0RT 1","EUR0SP0RT 2","HB0","HB0 2","HB0 3","DIVA","HAPPY TV",
    "FILM CAFE","TV 1000","FlLM N0W","EPIC DRAMA","AMC","SH0WTIME 1","SH0WTIME 2","AXN HD","AXN SPIN","AXN BLACK",
    "AXN WHITE","CINEMAX","CINEMAX 2","BOLYWOOD TV","COMEDY CENTRAL","WARNER TV","BBC FIRST","MINIMAX","CARTOON NETWORK",
    "DISNEY CHANNEL","TEEN NICK","NICKELODEON","DISNEY JR","CARTOONITO","NICKTOONS","NICK JR","JIM JAM","DUCK TV",
    "DISCOVERY CH","HISTORY CHANNEL","NAT GEO PEOPLE","NATIONAL GEOGRAPHIC","ID INVESTIGATION","CRIME & INVESTIGATION","TLC",
    "NAT GEO WILD","BBC EARTH","VIASAT HISTORY","VIASAT NATURE","VIASAT EXPLORER","TRAVEL MIX","PAPRIKA","HGTV","TARAF TV",
    "FAVORIT TV","ETNO TV","HIT MUSIC","BALCAN MUSIC","UTV","KISS TV","MUSIC CHANNEL","ZU TV","Fish & Hunting TV",
    "F1lm B0X","F1lm B0X Extra","F1lm B0X Premium","Canal 33","Dizi"
]

ALIASES = {
    "PROTV HD": ["pro tv", "protv", "pro tv hd", "pro tv.ro"],
    "A1 HD": ["antena 1", "a1", "antena 1 hd"],
    "TVR1": ["tvr 1", "tvr1", "tvr 1.ro"],
    "TVR2": ["tvr 2", "tvr2"],
    "TVR 3": ["tvr 3", "tvr3"],
    "TVR INTERNATIONAL": ["tvr international"],
    "CANAL D": ["kanal d", "canal d"],
    "CANAL D2": ["kanal d2", "canal d2"],
    "A3 CNN": ["antena 3 cnn", "antena 3"],
    "NEWS24": ["digi 24", "digi24", "news24"],
    "REALITATEA TV": ["realitatea plus", "realitatea tv"],
    "SUPERSPORT 1": ["digi sport 1", "supersport 1"],
    "SUPERSPORT 2": ["digi sport 2", "supersport 2"],
    "SUPERSPORT 3": ["digi sport 3", "supersport 3"],
    "SUPERSPORT 4": ["digi sport 4", "supersport 4"],
    "EUR0SP0RT 1": ["eurosport 1"],
    "EUR0SP0RT 2": ["eurosport 2"],
    "HB0": ["hbo"],
    "HB0 2": ["hbo 2"],
    "HB0 3": ["hbo 3"],
    "TV 1000": ["tv1000", "viasat kino", "kino tv"],
    "FlLM N0W": ["film now"],
    "F1lm B0X": ["filmbox", "film box"],
    "F1lm B0X Extra": ["filmbox extra", "film box extra"],
    "F1lm B0X Premium": ["filmbox premium", "film box premium"],
}


def normalize(value: str) -> str:
    value = (value or "").lower()
    value = value.replace("0", "o")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_xmltv_date(text: str):
    if not text:
        return None
    m = re.match(r"^(\d{14})(?:\s+([+\-]\d{4}))?$", text.strip())
    if not m:
        return None
    base = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    tz = m.group(2)
    if tz:
        sign = 1 if tz[0] == "+" else -1
        hours = int(tz[1:3])
        mins = int(tz[3:5])
        offset = timezone(sign * timedelta(hours=hours, minutes=mins))
        base = base.replace(tzinfo=offset).astimezone(timezone.utc)
    else:
        base = base.replace(tzinfo=timezone.utc)
    return base


def fetch_url(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
        encoding = resp.headers.get("Content-Encoding", "")
        content_type = resp.headers.get("Content-Type", "")
        if url.endswith(".gz") or "gzip" in encoding or "application/x-gzip" in content_type:
            data = gzip.decompress(data)
        return data


def iter_display_names(channel_elem):
    for node in channel_elem.findall("display-name"):
        if node.text and node.text.strip():
            yield node.text.strip()


def best_mapping(root):
    xml_channels = []
    for ch in root.findall("channel"):
        channel_id = ch.attrib.get("id", "")
        names = list(iter_display_names(ch))
        if channel_id and names:
            xml_channels.append((channel_id, names))

    matched = {}
    unresolved = []
    for local in CHANNELS:
        candidates = [normalize(local)] + [normalize(v) for v in ALIASES.get(local, [])]
        found = None
        for channel_id, names in xml_channels:
            normalized_names = [normalize(n) for n in names]
            if any(c and (c in n or n in c) for c in candidates for n in normalized_names):
                found = (channel_id, names[0])
                break
        if found:
            matched[local] = {"id": found[0], "name": found[1]}
        else:
            unresolved.append(local)
    return matched, unresolved


def build_json(root, source_url: str):
    matched, unresolved = best_mapping(root)
    now = datetime.now(timezone.utc) - timedelta(hours=3)
    horizon = now + timedelta(days=2)
    by_id = {}
    for prog in root.findall("programme"):
        channel_id = prog.attrib.get("channel", "")
        start = parse_xmltv_date(prog.attrib.get("start", ""))
        stop = parse_xmltv_date(prog.attrib.get("stop", ""))
        if not start:
            continue
        if stop and stop < now:
            continue
        if start > horizon:
            continue
        title_node = prog.find("title")
        if title_node is None or not (title_node.text or "").strip():
            continue
        title = title_node.text.strip()
        desc = (prog.findtext("desc") or "").strip()
        category = (prog.findtext("category") or "").strip()
        by_id.setdefault(channel_id, []).append({
            "start": start.isoformat().replace("+00:00", "Z"),
            "stop": stop.isoformat().replace("+00:00", "Z") if stop else None,
            "title": title,
            "details": " • ".join([v for v in [category, desc[:160] if desc else ""] if v])
        })

    channels_out = {}
    count = 0
    for local in CHANNELS:
        channel_id = matched.get(local, {}).get("id")
        items = sorted(by_id.get(channel_id, []), key=lambda x: x["start"])
        channels_out[local] = items
        count += len(items)

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": source_url,
        "matched": matched,
        "unresolved": unresolved,
        "channels": channels_out,
        "programmeCount": count,
    }


def main():
    best_payload = None
    best_count = -1
    last_error = None
    for url in XMLTV_SOURCES:
        try:
            data = fetch_url(url)
            root = ET.fromstring(data)
            payload = build_json(root, url)
            if payload["programmeCount"] > best_count:
                best_payload = payload
                best_count = payload["programmeCount"]
            print(f"Source {url} -> {payload['programmeCount']} programe utile")
        except Exception as exc:
            last_error = exc
            print(f"Source failed: {url} -> {exc}", file=sys.stderr)

    if not best_payload:
        raise SystemExit(f"Nu am putut genera epg.json: {last_error}")

    best_payload.pop("programmeCount", None)
    OUT.write_text(json.dumps(best_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Scris {OUT} din {best_payload['source']}")

if __name__ == "__main__":
    main()

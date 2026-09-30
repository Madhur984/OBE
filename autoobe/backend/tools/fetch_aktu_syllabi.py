"""
Download the B.Tech syllabi AKTU publishes, so the catalogue can be rebuilt.

    python tools/fetch_aktu_syllabi.py <folder>

Run when the university publishes a new year, then rebuild:

    python tools/build_aktu_catalogue.py <folder>

The university keeps one index page per session at
`https://aktu.ac.in/syllabus <year>.html`, each linking the PDFs for that
session. This walks those pages rather than guessing filenames, because the
filenames are not guessable -- "1 B.Tech. 3rd Year Electrical Engineering
2024-25.pdf" and "B.Tech_3rd_year_PE_and_IPE_Evaluation_Scheme_2024-25.pdf"
sit in the same folder.

Nothing here runs at request time. The tool itself never reaches the network.
"""
from __future__ import annotations

import html
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://aktu.ac.in/"
# Sessions worth having. The older three carry the courses the newer documents
# assume you already have -- first year, the common HSMC and non-credit papers,
# Technical Communication -- which is exactly where a workbook's non-technical
# blocks come from.
YEARS = ("2021-2022", "2022-2023", "2023-2024",
         "2024-2025", "2025-2026", "2026-2027")

# Everything B.Tech, plus the common papers that sit outside a branch document.
_WANTED = re.compile(
    r"b\.?\s?tech|first|1st|2nd|3rd|4th|second|third|final|common|"
    r"non.?credit|nc.course|hsmc|elective|human|minor|stream|"
    r"technical.?communication|bcc|uhv|kve|syllabus", re.I)
# Other programmes. They have their own accreditation and their own outcomes,
# and mixing them into a B.Tech catalogue is how a workbook ends up offered
# the syllabus for an MBA paper.
_UNWANTED = re.compile(r"\b(MBA|MCA|BBA|BCA|BFA|BFAD|B\.?Arch|Pharm|M\.?Tech)\b", re.I)

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def _get(url: str, timeout: int = 90) -> bytes:
    req = urllib.request.Request(
        urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%"),
        headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=timeout, context=_CTX).read()


def links_for(year: str) -> dict[str, str]:
    """Every PDF the session's index page offers, url -> link text."""
    try:
        page = _get(f"{BASE}syllabus%20{year}.html", timeout=60).decode(
            "utf-8", errors="replace")
    except Exception as exc:                            # noqa: BLE001
        print(f"  !! index {year}: {type(exc).__name__} {exc}")
        return {}
    out: dict[str, str] = {}
    for m in re.finditer(r'<a[^>]+href="([^"]+\.pdf)"[^>]*>(.*?)</a>',
                         page, re.I | re.S):
        url = m.group(1)
        text = re.sub(r"\s+", " ",
                      html.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))).strip()
        if not url.startswith("http"):
            url = BASE + url.lstrip("/")
        out.setdefault(url, text)
    return out


def wanted(url: str, text: str) -> bool:
    blob = urllib.parse.unquote(url.rsplit("/", 1)[-1]) + " " + text
    return bool(_WANTED.search(blob)) and not _UNWANTED.search(blob)


def main() -> None:
    dest = Path(sys.argv[1] if len(sys.argv) > 1
                else r"d:/Hokage X Pirate king/OBE/aktu_corpus")
    dest.mkdir(parents=True, exist_ok=True)

    targets: dict[str, str] = {}
    for year in YEARS:
        found = links_for(year)
        keep = {u: year for u, t in found.items() if wanted(u, t)}
        print(f"{year}: {len(found)} links, {len(keep)} B.Tech")
        for u, y in keep.items():
            targets.setdefault(u, y)

    ok = cached = failed = 0
    for url, year in sorted(targets.items()):
        name = urllib.parse.unquote(url.rsplit("/", 1)[-1])
        name = re.sub(r"[^A-Za-z0-9._&()-]+", "_", name).strip("_")
        out = dest / f"{year[:4]}_{name}"
        if out.exists() and out.stat().st_size > 20_000:
            cached += 1
            continue
        try:
            data = _get(url)
        except Exception as exc:                        # noqa: BLE001
            failed += 1
            print(f"  !! {name[:60]}: {type(exc).__name__} {exc}")
            continue
        if not data.startswith(b"%PDF"):
            failed += 1
            print(f"  !! not a pdf: {name[:60]}")
            continue
        out.write_bytes(data)
        ok += 1
        print(f"  ok {len(data) // 1024:>5}KB  {out.name[:70]}")

    print(f"\n{ok} downloaded, {cached} already held, {failed} failed -> {dest}")
    print("now run: python tools/build_aktu_catalogue.py "
          f'"{dest}"')


if __name__ == "__main__":
    main()

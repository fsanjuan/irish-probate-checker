#!/usr/bin/env python3
"""
Check each person from a rip.ie JSON file against the courts.ie probate register.
Outputs a JSON file with the search performed and any grants found.

Usage:
    python3 check_probate.py rathfarnham_2025.json
    python3 check_probate.py rathfarnham_2025.json --output probate_results.json
    python3 check_probate.py rathfarnham_2025.json --delay 1.0 --year-offset 1
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html import unescape


PROBATE_URL = "https://courts.ie/app/probate-register"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IE,en;q=0.5",
    "Referer": "https://courts.ie/app/probate-register",
}


# ---------------------------------------------------------------------------
# Name normalisation helpers
# ---------------------------------------------------------------------------

def normalise_surname(raw: str) -> str:
    """
    Convert all-caps or mixed-caps surnames to title case, preserving
    Irish/Scottish prefixes (O', Mc, Mac).
    E.g. "FLANAGAN" → "Flanagan", "O'SULLIVAN" → "O'Sullivan", "McAULIFFE" → "McAuliffe"
    """
    s = raw.strip()
    if not s:
        return s

    # Strip any parenthetical nickname rip.ie sometimes puts in the surname field
    # e.g. "O'Sullivan (Nickname)" → "O'Sullivan"
    s = re.sub(r"\s*\([^)]*\)", "", s).strip()

    # Title-case word by word, handling apostrophes and hyphens
    def cap_word(w: str) -> str:
        # Handle prefixes: Mc, Mac, O'
        for prefix in ("Mac", "Mc", "mac", "mc", "MAC", "MC"):
            if w.lower().startswith(prefix.lower()) and len(w) > len(prefix):
                rest = w[len(prefix):]
                return prefix.title() + rest[0].upper() + rest[1:].lower()
        if "'" in w:
            parts = w.split("'", 1)
            return parts[0].title() + "'" + parts[1].title()
        return w.title()

    parts = re.split(r"([-\s])", s)
    result = "".join(cap_word(p) if re.match(r"[A-Za-z]", p) else p for p in parts)
    return result


def extract_surname_variants(raw: str) -> list[str]:
    """
    Generate search variants for Irish/Scottish surnames following courts.ie guidance:
    - Mc/Mac: try both spaced ("Mc Auliffe", "Mac Giolla") and unspaced ("McAuliffe", "MacGiolla")
    - O': try without prefix ("O'Sullivan" → also "Sullivan")
    - Ó: try without accent ("Ó Murchú" → also "O Murchú") and without prefix ("Murchú")
    - Ní/Nic/Uí: try without accent and without prefix

    Examples:
        "McAuliffe"  → ["McAuliffe", "Mc Auliffe", "Auliffe"]
        "Mac Giolla" → ["Mac Giolla","MacGiolla",  "Giolla"]
        "O'Sullivan" → ["O'Sullivan","Sullivan"]
        "Ó Murchú"   → ["Ó Murchú",  "O Murchú",   "Murchú"]
        "Ní Fhaoláin"→ ["Ní Fhaoláin","Ni Fhaoláin","Fhaoláin"]
        "Flanagan"   → ["Flanagan"]
    """
    normalised = normalise_surname(raw)
    variants: list[str] = [normalised]

    s = normalised

    # --- Mc / Mac (with or without space) ---
    # Matches "McAuliffe", "Mac Giolla", "MacGiolla" etc.
    mc_match = re.match(r'^(Mac|Mc)\s*([A-ZÁÉÍÓÚ][a-záéíóú].*)', s)
    if mc_match:
        prefix = mc_match.group(1)   # "Mac" or "Mc"
        stem = mc_match.group(2)     # "Auliffe", "Giolla" etc.
        unspaced = prefix + stem          # "McAuliffe" / "MacGiolla"
        spaced = prefix + " " + stem     # "Mc Auliffe" / "Mac Giolla"
        for v in (unspaced, spaced, stem):
            if v.lower() not in {x.lower() for x in variants}:
                variants.append(v)

    # --- O' ---
    elif re.match(r"^O'", s):
        stem = s[2:]   # everything after "O'"
        if stem and stem.lower() not in {x.lower() for x in variants}:
            variants.append(stem)

    # --- Ó (fada) ---
    elif re.match(r'^Ó\s+', s):
        stem = s[2:].strip()   # everything after "Ó "
        o_plain = "O " + stem  # "O Murchú"
        for v in (o_plain, stem):
            if v.lower() not in {x.lower() for x in variants}:
                variants.append(v)

    # --- Ní ---
    elif re.match(r'^Ní\s+', s):
        stem = s[3:].strip()
        ni_plain = "Ni " + stem
        for v in (ni_plain, stem):
            if v.lower() not in {x.lower() for x in variants}:
                variants.append(v)

    # --- Nic ---
    elif re.match(r'^Nic\s+', s):
        stem = s[4:].strip()
        if stem.lower() not in {x.lower() for x in variants}:
            variants.append(stem)

    # --- Uí ---
    elif re.match(r'^Uí\s+', s):
        stem = s[3:].strip()
        if stem.lower() not in {x.lower() for x in variants}:
            variants.append(stem)

    return variants


def extract_firstname_variants(raw: str) -> list[str]:
    """
    Extract meaningful search variants from a raw first name string.

    Examples:
        "Jim (James)"           → ["Jim", "James"]
        "Mary (Mai)"            → ["Mary", "Mai"]
        "Jo/Josie"              → ["Jo", "Josie"]
        "Pat (Patricia)"        → ["Pat", "Patricia"]
        "Dr. Adrian"            → ["Adrian"]
        "Sr. Mary"              → ["Mary"]
        "Prof. Charles (Cathal)"→ ["Charles", "Cathal"]
        "Michael (Mick)"        → ["Michael", "Mick"]
        "John Anthony"          → ["John"]
        "Dorothy Ida"           → ["Dorothy"]
        "Seamus (Shay)"         → ["Seamus", "Shay"]
    """
    s = raw.strip()

    # Strip honourifics / titles
    honourifics = r"^(?:Dr\.|Prof\.|Sr\.|Br\.|Fr\.|Rev\.|Mr\.|Mrs\.|Ms\.)\s*"
    s = re.sub(honourifics, "", s, flags=re.IGNORECASE).strip()

    variants: list[str] = []

    # Handle slash-separated names: "Jo/Josie"
    if "/" in s:
        for part in s.split("/"):
            part = part.strip()
            if part:
                variants.extend(extract_firstname_variants(part))
        return _dedupe(variants)

    # Extract name in parentheses as an alternate
    parens_match = re.search(r"\(([^)]+)\)", s)
    alternate = parens_match.group(1).strip() if parens_match else None

    # Primary name = everything before the parenthesis
    primary = re.sub(r"\s*\([^)]*\)", "", s).strip()
    # Use only the first token (first given name)
    primary_first = primary.split()[0] if primary.split() else primary

    if primary_first:
        variants.append(primary_first)
    if alternate and alternate.lower() != primary_first.lower():
        variants.append(alternate)

    return _dedupe(variants)


def _dedupe(lst: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in lst:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

def clean_text(s: str) -> str:
    """Strip HTML tags, unescape entities, collapse whitespace."""
    s = re.sub(r"<[^>]+>", " ", s)
    s = unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    # Remove non-breaking space bullet separators used by courts.ie
    s = s.replace("\xa0", " ").replace("•", "•")
    return s


def parse_grants(html: str) -> list[dict]:
    """
    Parse all grant entries from the courts.ie results page.
    Returns a list of dicts with keys:
        full_name, date_of_death, grant_type, address,
        case_ref, date_issued, grantees
    """
    grants = []
    entries = re.findall(
        r'<li class="row gx-0 probate-grants-entity mb-3">(.*?)</li>',
        html,
        re.DOTALL,
    )
    for entry in entries:
        grant: dict = {}

        # Full name + date of death from the h4 title
        title_match = re.search(
            r'<h4 class="probate-grants-entity-title">(.*?)</h4>', entry, re.DOTALL
        )
        if title_match:
            title_text = clean_text(title_match.group(1))
            # Format: "Patrick Flanagan   •   15/03/2025"
            parts = [p.strip() for p in re.split(r"•", title_text) if p.strip()]
            grant["full_name"] = parts[0] if parts else ""
            grant["date_of_death"] = parts[1] if len(parts) > 1 else ""

        # Grant type (Probate / Intestate)
        label_match = re.search(
            r'<span class="probate-grants-entity-label[^"]*">(.*?)</span>',
            entry,
            re.DOTALL,
        )
        grant["grant_type"] = clean_text(label_match.group(1)) if label_match else ""

        # Address and case reference are in the same div
        addr_div = re.search(
            r'<div class="col-xl-8[^"]*">\s*(Address:.*?)\s*</div>',
            entry,
            re.DOTALL,
        )
        if addr_div:
            addr_raw = clean_text(addr_div.group(1))
            # Split on "Case ref.:"
            if "Case ref.:" in addr_raw:
                addr_part, case_part = addr_raw.split("Case ref.:", 1)
                grant["address"] = addr_part.replace("Address:", "").strip()
                grant["case_ref"] = case_part.strip()
            else:
                grant["address"] = addr_raw.replace("Address:", "").strip()
                grant["case_ref"] = ""

        # Date issued
        issued_match = re.search(r"Issued:.*?<strong>(.*?)</strong>", entry, re.DOTALL)
        grant["date_issued"] = clean_text(issued_match.group(1)) if issued_match else ""

        # Grantees
        grantees_match = re.search(
            r'<h5 class="probate-grants-entity-subtitle">.*?Grantees.*?</h5>\s*<p>(.*?)</p>',
            entry,
            re.DOTALL,
        )
        if grantees_match:
            raw = clean_text(grantees_match.group(1))
            # Grantees are separated by bullets
            grantees = [g.strip() for g in re.split(r"•", raw) if g.strip()]
            grant["grantees"] = grantees
        else:
            grant["grantees"] = []

        grants.append(grant)

    return grants


def parse_grants_count(html: str) -> int:
    match = re.search(r"Grants found:\s*(\d+)", html)
    return int(match.group(1)) if match else 0


def parse_total_pages(html: str) -> int:
    match = re.search(r"Page \d+ of (\d+)", html)
    return int(match.group(1)) if match else 1


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def fetch_probate(firstname: str, lastname: str, year: int | str, page: int = 1) -> str:
    params = {
        "firstname": firstname,
        "lastname": lastname,
        "year": str(year),
    }
    if page > 1:
        params["page"] = str(page)

    url = PROBATE_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Request failed: {e}")


def search_probate(firstname: str, lastname: str, year: int | str) -> dict:
    """
    Search courts.ie and return a structured result dict.
    Handles multi-page results automatically.
    """
    html = fetch_probate(firstname, lastname, year, page=1)
    total_pages = parse_total_pages(html)
    total_count = parse_grants_count(html)
    grants = parse_grants(html)

    for page in range(2, total_pages + 1):
        time.sleep(0.5)
        extra_html = fetch_probate(firstname, lastname, year, page=page)
        grants.extend(parse_grants(extra_html))

    return {
        "search": {
            "firstname": firstname,
            "lastname": lastname,
            "year": str(year),
            "url": (
                PROBATE_URL
                + "?"
                + urllib.parse.urlencode(
                    {"firstname": firstname, "lastname": lastname, "year": str(year)}
                )
            ),
        },
        "grants_found": total_count,
        "grants": grants,
    }


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

def compute_input_hash(path: str) -> str:
    """Return the SHA-256 hex digest of a file's raw bytes."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_checkpoint(
    output_path: str, input_hash: str
) -> tuple[set, list, bool]:
    """
    Try to load a previous run from *output_path*.

    Returns (done_ids, existing_results, already_complete) where:
      - done_ids          — set of rip.ie person IDs already processed
      - existing_results  — list of result dicts already written
      - already_complete  — True if the previous run finished cleanly

    Returns (set(), [], False) when there is nothing useful to resume from.
    """
    if not os.path.exists(output_path):
        return set(), [], False

    try:
        with open(output_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        print(f"Warning: could not read existing output file — starting fresh.")
        return set(), [], False

    if data.get("input_hash") != input_hash:
        print("Existing output file is for a different input — starting fresh.")
        return set(), [], False

    existing_results = data.get("results", [])
    done_ids = {
        r["rip_ie"]["id"]
        for r in existing_results
        if "rip_ie" in r and r["rip_ie"].get("id") is not None
    }
    already_complete = bool(data.get("is_complete", False))
    return done_ids, existing_results, already_complete


def _write_output(
    output_path: str,
    input_path: str,
    input_hash: str,
    results: list,
    total_searches: int,
    total_found: int,
    is_complete: bool,
    only_matches: bool,
) -> None:
    """Write results to *output_path*.

    Intermediate checkpoint writes always include every result so that
    resume logic can identify which persons have already been processed.
    The *only_matches* filter is applied only on the final (complete) write.
    """
    matched = [r for r in results if r.get("probate_found")]
    output_results = matched if (is_complete and only_matches) else results

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "input_file": input_path,
        "input_hash": input_hash,
        "is_complete": is_complete,
        "summary": {
            "persons_checked": len(results),
            "total_searches": total_searches,
            "total_grants_found": total_found,
            "persons_with_grants": len(matched),
        },
        "results": output_results,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def build_searches(person: dict, year_offset: int = 0) -> list[tuple[str, str, str]]:
    """
    Build a list of (firstname, lastname, year) search tuples for a person,
    trying name variants and optionally adjacent years.
    Returns unique combinations only.
    """
    raw_firstname = person.get("firstname", "")
    raw_surname = person.get("surname", "")
    year_raw = person.get("year_of_death", "")

    if not raw_surname or not year_raw:
        return []

    firstname_variants = extract_firstname_variants(raw_firstname)
    surname_variants = extract_surname_variants(raw_surname)

    if not firstname_variants:
        return []

    # Build year list (the given year ± offset)
    try:
        base_year = int(year_raw)
    except ValueError:
        return []

    years = [base_year]
    for offset in range(1, year_offset + 1):
        years.append(base_year - offset)
        years.append(base_year + offset)

    seen = set()
    searches = []
    for fn in firstname_variants:
        for sn in surname_variants:
            for yr in years:
                key = (fn.lower(), sn.lower(), str(yr))
                if key not in seen:
                    seen.add(key)
                    searches.append((fn, sn, str(yr)))

    return searches


def process_file(
    input_path: str,
    output_path: str,
    delay: float,
    year_offset: int,
    only_matches: bool,
    checkpoint_every: int = 10,
) -> None:
    # Compute a fingerprint of the input so we can detect stale checkpoints.
    input_hash = compute_input_hash(input_path)

    # Load input
    with open(input_path, "r", encoding="utf-8") as f:
        persons = json.load(f)

    print(f"Loaded {len(persons)} records from {input_path}")

    # Deduplicate by (firstname, surname, year_of_death) so we don't repeat
    # identical searches for people listed under multiple towns.
    unique_persons: dict[tuple, dict] = {}
    for p in persons:
        key = (
            p.get("firstname", "").lower(),
            p.get("surname", "").lower(),
            p.get("year_of_death", ""),
        )
        if key not in unique_persons:
            unique_persons[key] = p

    deduped = list(unique_persons.values())
    print(f"Unique persons to check: {len(deduped)}")

    # Resume from checkpoint if one exists for this exact input.
    done_ids, results, already_complete = load_checkpoint(output_path, input_hash)

    if already_complete:
        print(f"Previous run already completed. Results are in: {output_path}")
        print("Delete the output file or use --output to specify a new path to re-run.")
        return

    if done_ids:
        print(f"Resuming: {len(done_ids)} persons already done, skipping them.")

    # Initialise running totals from the existing checkpoint results.
    total_searches = sum(len(r.get("probate_searches", [])) for r in results)
    total_found = sum(
        s.get("grants_found", 0)
        for r in results
        for s in r.get("probate_searches", [])
    )
    newly_processed = 0

    for i, person in enumerate(deduped, 1):
        pid = person.get("id")

        if pid in done_ids:
            print(f"  [{i}/{len(deduped)}] Already done: {person.get('firstname')} {person.get('surname')}")
            continue

        searches = build_searches(person, year_offset=year_offset)
        if not searches:
            print(f"  [{i}/{len(deduped)}] Skipping (no searchable name): {person.get('firstname')} {person.get('surname')}")
            continue

        person_result = {
            "rip_ie": {
                "id": pid,
                "firstname": person.get("firstname"),
                "surname": person.get("surname"),
                "nee": person.get("nee"),
                "town": person.get("town"),
                "county": person.get("county"),
                "date_of_death": person.get("date_of_death"),
                "year_of_death": person.get("year_of_death"),
                "date_published": person.get("date_published"),
                "url": person.get("url"),
            },
            "probate_searches": [],
        }

        any_found = False

        for firstname, lastname, year in searches:
            total_searches += 1
            label = f"{firstname} {lastname} ({year})"
            print(f"  [{i}/{len(deduped)}] Searching: {label}...", end=" ", flush=True)

            try:
                result = search_probate(firstname, lastname, year)
            except RuntimeError as e:
                print(f"ERROR: {e}")
                result = {
                    "search": {
                        "firstname": firstname,
                        "lastname": lastname,
                        "year": year,
                        "url": "",
                    },
                    "grants_found": 0,
                    "grants": [],
                    "error": str(e),
                }

            person_result["probate_searches"].append(result)

            count = result.get("grants_found", 0)
            print(f"{'✓ ' + str(count) + ' grant(s) found' if count else 'not found'}")

            if count > 0:
                any_found = True
                total_found += count

            time.sleep(delay)

        person_result["probate_found"] = any_found
        results.append(person_result)
        newly_processed += 1

        # Periodic checkpoint — always write all results so resume works.
        if newly_processed % checkpoint_every == 0:
            _write_output(
                output_path, input_path, input_hash,
                results, total_searches, total_found,
                is_complete=False, only_matches=False,
            )
            print(f"  [checkpoint] Saved progress ({len(results)} persons done)")

    # Final write
    matched = [r for r in results if r.get("probate_found")]
    print(f"\n{'='*60}")
    print(f"Persons checked:     {len(deduped)}")
    print(f"Total searches made: {total_searches}")
    print(f"Total grants found:  {total_found}")
    print(f"Persons with grants: {len(matched)}")

    _write_output(
        output_path, input_path, input_hash,
        results, total_searches, total_found,
        is_complete=True, only_matches=only_matches,
    )
    print(f"\nOutput saved to: {output_path}")

    # Print persons with grants
    if matched:
        print("\n--- Persons with probate grants ---")
        for r in matched:
            rip = r["rip_ie"]
            print(f"\n  {rip['firstname']} {rip['surname']} ({rip['year_of_death']}) — {rip['town']}")
            print(f"  rip.ie: {rip['url']}")
            for search in r["probate_searches"]:
                if search.get("grants_found", 0) > 0:
                    print(f"  Searched: {search['search']['firstname']} {search['search']['lastname']} {search['search']['year']}")
                    for g in search["grants"]:
                        print(f"    → {g['grant_type']}: {g['full_name']} | DoD: {g['date_of_death']} | Issued: {g['date_issued']}")
                        print(f"      Address: {g['address']}")
                        print(f"      Case ref: {g['case_ref']}")
                        print(f"      Grantees: {', '.join(g['grantees'])}")


def main():
    parser = argparse.ArgumentParser(
        description="Check rip.ie death notices against the courts.ie probate register.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 check_probate.py rathfarnham_2025.json
  python3 check_probate.py rathfarnham_2025.json --output results.json
  python3 check_probate.py rathfarnham_2025.json --only-matches
  python3 check_probate.py rathfarnham_2025.json --year-offset 1 --delay 1.0
        """,
    )
    parser.add_argument("input", help="Input JSON file from scrape_rip.py")
    parser.add_argument(
        "--output", "-o",
        default="",
        help="Output JSON file path (default: <input_stem>_probate.json)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.8,
        help="Seconds to wait between requests (default: 0.8)",
    )
    parser.add_argument(
        "--year-offset",
        dest="year_offset",
        type=int,
        default=0,
        help=(
            "Also search ± N years around year_of_death "
            "(useful when death date straddles year end, default: 0)"
        ),
    )
    parser.add_argument(
        "--only-matches",
        dest="only_matches",
        action="store_true",
        help="Only include persons with at least one grant found in the output",
    )
    parser.add_argument(
        "--checkpoint-every",
        dest="checkpoint_every",
        type=int,
        default=10,
        help="Save progress to the output file every N persons (default: 10)",
    )

    args = parser.parse_args()

    input_path = args.input
    if not args.output:
        stem = os.path.splitext(os.path.basename(input_path))[0]
        output_path = f"{stem}_probate.json"
    else:
        output_path = args.output

    process_file(
        input_path=input_path,
        output_path=output_path,
        delay=args.delay,
        year_offset=args.year_offset,
        only_matches=args.only_matches,
        checkpoint_every=args.checkpoint_every,
    )


if __name__ == "__main__":
    main()

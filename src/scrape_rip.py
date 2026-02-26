#!/usr/bin/env python3
"""
Scrape death notices from rip.ie for a given area and date range.
Outputs CSV and/or JSON for use with the courts.ie probate register search.

Usage:
    python3 scrape_rip.py --town rathfarnham --county dublin --year 2025
    python3 scrape_rip.py --town rathfarnham --county dublin --from-date 2025-01-01 --to-date 2025-12-31
    python3 scrape_rip.py --help
"""

import argparse
import csv
import json
import sys
import time
import urllib.request
from datetime import datetime, date


GRAPHQL_URL = "https://rip.ie/api/graphql"
NOTICE_BASE_URL = "https://www.rip.ie/death-notice"

HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://www.rip.ie",
    "Referer": "https://www.rip.ie/death-notice/s/dublin",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

LIST_QUERY = """
query searchDeathNoticesForList($list: ListInput!, $isTiledView: Boolean!) {
  searchDeathNoticesForList(query: $list, isTiledView: $isTiledView) {
    count
    perPage
    page
    nextPage
    records {
      id
      firstname
      surname
      nee
      createdAt
      dateOfDeath
      county { id name }
      town { id name }
    }
  }
}
"""

DETAIL_QUERY = """
query getDeathNoticeDetail($deathNoticeId: Float!) {
  previewDeathNotice(deathNoticeId: $deathNoticeId) {
    id
    firstname
    surname
    nee
    dateOfDeath
    dateOfBirth
    createdAt
    address
    county { id name }
    town { id name }
  }
}
"""


def graphql_request(payload: dict) -> dict:
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        GRAPHQL_URL, data=data_bytes, headers=HEADERS, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body[:500]}")


def fetch_notices_page(
    county: str,
    town_slug: str,
    from_date: str,
    to_date: str,
    page: int,
) -> dict:
    """Fetch one page of death notices from the list API."""
    filters = [
        {"field": "county.name", "operator": "eq", "value": county},
        {"field": "a.createdAt", "operator": "gte", "value": f"{from_date} 00:00:00"},
        {"field": "a.createdAt", "operator": "lte", "value": f"{to_date} 23:59:59"},
    ]
    if town_slug:
        filters.append({"field": "town.slug", "operator": "eq", "value": town_slug})

    payload = {
        "operationName": "searchDeathNoticesForList",
        "query": LIST_QUERY,
        "variables": {
            "isTiledView": False,
            "list": {
                "page": page,
                "filters": filters,
                "orders": [{"field": "a.createdAtCastToDate", "type": "DESC"}],
            },
        },
    }
    result = graphql_request(payload)
    if "errors" in result and result.get("data") is None:
        raise RuntimeError(f"GraphQL error: {result['errors'][0]['message']}")
    return result["data"]["searchDeathNoticesForList"]


def fetch_notice_detail(notice_id: int) -> dict | None:
    """Fetch a single notice to get dateOfDeath if not in the list."""
    payload = {
        "operationName": "getDeathNoticeDetail",
        "query": DETAIL_QUERY,
        "variables": {"deathNoticeId": float(notice_id)},
    }
    try:
        result = graphql_request(payload)
        if "errors" in result and result.get("data") is None:
            return None
        return result["data"].get("previewDeathNotice")
    except RuntimeError:
        return None


def build_notice_url(notice: dict) -> str:
    """Build the rip.ie URL for a notice."""
    first = notice.get("firstname", "").lower().replace(" ", "-").replace("(", "").replace(")", "")
    surname = notice.get("surname", "").lower()
    county = notice.get("county", {}).get("name", "").lower()
    town = notice.get("town", {}).get("name", "").lower().replace(" ", "-")
    nid = notice.get("id", "")
    return f"{NOTICE_BASE_URL}/{first}-{surname}-{county}-{town}-{nid}"


def scrape(
    county: str,
    town_slug: str,
    from_date: str,
    to_date: str,
    fetch_details: bool,
    delay: float,
) -> list[dict]:
    """Scrape all pages and return a list of notice records."""
    all_records = []
    page = 1
    seen_ids = set()

    print(f"Searching: county={county}, town={town_slug or 'all'}, {from_date} → {to_date}")

    while True:
        print(f"  Fetching page {page}...", end=" ", flush=True)
        try:
            data = fetch_notices_page(county, town_slug, from_date, to_date, page)
        except RuntimeError as e:
            print(f"\nError on page {page}: {e}")
            break

        records = data.get("records", [])
        if not records:
            print("no records, done.")
            break

        new_count = 0
        for r in records:
            if r["id"] in seen_ids:
                continue
            seen_ids.add(r["id"])

            # The list query sometimes returns dateOfDeath=null even when it exists
            date_of_death = r.get("dateOfDeath")

            if fetch_details and date_of_death is None:
                time.sleep(delay * 0.5)  # smaller delay for detail requests
                detail = fetch_notice_detail(r["id"])
                if detail:
                    date_of_death = detail.get("dateOfDeath")

            # Parse dates
            published_dt = r.get("createdAt", "")
            published_date = published_dt[:10] if published_dt else ""
            published_year = published_dt[:4] if published_dt else ""

            death_date = ""
            death_year = ""
            if date_of_death:
                death_date = date_of_death[:10]
                death_year = date_of_death[:4]
            elif published_year:
                # Fall back to year of publication as best estimate
                death_year = published_year

            notice_url = build_notice_url(r)

            record = {
                "id": r["id"],
                "firstname": r.get("firstname", ""),
                "surname": r.get("surname", ""),
                "nee": r.get("nee", ""),
                "town": r.get("town", {}).get("name", ""),
                "county": r.get("county", {}).get("name", ""),
                "date_of_death": death_date,
                "year_of_death": death_year,
                "date_published": published_date,
                "url": notice_url,
            }
            all_records.append(record)
            new_count += 1

        print(f"{new_count} new records (total so far: {len(all_records)})")

        if not data.get("nextPage", False):
            break

        page += 1
        time.sleep(delay)

    return all_records


def save_csv(records: list[dict], path: str) -> None:
    if not records:
        print("No records to save.")
        return
    fieldnames = list(records[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)
    print(f"Saved {len(records)} records to {path}")


def save_json(records: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(records)} records to {path}")


def main():
    parser = argparse.ArgumentParser(
        description="Scrape rip.ie death notices for probate research.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scrape_rip.py --town rathfarnham --year 2025
  python3 scrape_rip.py --town rathfarnham --county dublin --from-date 2025-06-01 --to-date 2025-12-31
  python3 scrape_rip.py --county dublin --year 2025 --no-details --output-json all_dublin_2025.json
        """,
    )
    parser.add_argument(
        "--town",
        default="",
        help="Town slug (e.g. 'rathfarnham', 'terenure'). Leave empty for entire county.",
    )
    parser.add_argument(
        "--county",
        default="dublin",
        help="County name (default: dublin)",
    )
    parser.add_argument(
        "--year",
        type=int,
        help="Year to search (e.g. 2025). Sets from-date and to-date automatically.",
    )
    parser.add_argument(
        "--from-date",
        dest="from_date",
        help="Start date in YYYY-MM-DD format",
    )
    parser.add_argument(
        "--to-date",
        dest="to_date",
        help="End date in YYYY-MM-DD format",
    )
    parser.add_argument(
        "--no-details",
        action="store_true",
        help="Skip individual notice fetches (faster but may miss dateOfDeath)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay in seconds between page requests (default: 0.5)",
    )
    parser.add_argument(
        "--output-csv",
        dest="output_csv",
        metavar="FILE",
        help="Output CSV file path (default: <town>_<year>.csv)",
    )
    parser.add_argument(
        "--output-json",
        dest="output_json",
        metavar="FILE",
        help="Output JSON file path (default: <town>_<year>.json)",
    )
    parser.add_argument(
        "--format",
        choices=["csv", "json", "both"],
        default="both",
        help="Output format (default: both)",
    )

    args = parser.parse_args()

    # Date range
    if args.year:
        from_date = f"{args.year}-01-01"
        to_date = f"{args.year}-12-31"
    elif args.from_date and args.to_date:
        from_date = args.from_date
        to_date = args.to_date
    else:
        # Default: current year
        year = date.today().year
        from_date = f"{year}-01-01"
        to_date = f"{year}-12-31"
        print(f"No date specified, defaulting to year {year}")

    town_slug = args.town.lower().replace(" ", "-")
    county = args.county.lower()

    # Output file names
    label = f"{town_slug or county}_{from_date[:4]}"
    csv_path = args.output_csv or f"{label}.csv"
    json_path = args.output_json or f"{label}.json"

    # Scrape
    records = scrape(
        county=county,
        town_slug=town_slug,
        from_date=from_date,
        to_date=to_date,
        fetch_details=not args.no_details,
        delay=args.delay,
    )

    if not records:
        print("No records found.")
        sys.exit(0)

    print(f"\nTotal unique notices: {len(records)}")

    # Save
    if args.format in ("csv", "both"):
        save_csv(records, csv_path)
    if args.format in ("json", "both"):
        save_json(records, json_path)

    # Print a summary for probate searching
    print("\n--- Summary (for probate search) ---")
    print(f"{'Firstname':<25} {'Surname':<20} {'Year of Death':<15} {'Town'}")
    print("-" * 75)
    for r in records:
        print(
            f"{r['firstname']:<25} {r['surname']:<20} {r['year_of_death']:<15} {r['town']}"
        )


if __name__ == "__main__":
    main()

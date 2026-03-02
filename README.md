# irish-probate-checker

A two-script toolkit for researching probate status on Irish properties.

**Use case:** You are buying a house that comes from a probate sale and want to verify that a grant of probate has been issued. You know the general area the deceased lived in but not their name. This toolkit scrapes [rip.ie](https://rip.ie) for death notices in that area, then checks each person against the [courts.ie probate register](https://courts.ie/app/probate-register).

---

## Background

### What is probate?

When someone dies in Ireland, probate is the legal process that validates their will and authorises the executor to deal with the estate (including selling property). If there is no will, a similar process called a **grant of administration** (intestate) applies.

Before a property from an estate can be sold, a grant must be issued by the Probate Office. You can verify this on the public [courts.ie probate register](https://courts.ie/app/probate-register), which requires the deceased's **first name**, **last name**, and **year of death**.

### How this toolkit helps

1. **`scrape_rip.py`** — searches [rip.ie](https://rip.ie) (Ireland's main death notice website) for all notices published in a given area and year, and saves them to CSV and JSON.
2. **`check_probate.py`** — takes that JSON and checks each person against the courts.ie probate register, saving a full results JSON with any grants found.

---

## Requirements

- Python 3.10+
- `requests` library

```bash
pip3 install requests --break-system-packages
```

---

## Running with Docker

If you'd rather not install anything, you can use the Docker image instead.

```bash
# Build the image
docker build --target app -t rip-probate:app .
```

Create a local directory for output files, then mount it when running:

```bash
mkdir output

# Step 1 — scrape death notices
docker run --rm -v $(pwd)/output:/app/output rip-probate:app \
  python src/scrape_rip.py --town rathfarnham --year 2025 \
  --output-csv output/rathfarnham_2025.csv \
  --output-json output/rathfarnham_2025.json

# Step 2 — check probate status
docker run --rm -v $(pwd)/output:/app/output rip-probate:app \
  python src/check_probate.py output/rathfarnham_2025.json \
  --output output/rathfarnham_2025_probate.json
```

Results will appear in your local `output/` directory.

---

## Script 1: scrape_rip.py

Fetches death notices from rip.ie for a given area and date range.

### Usage

```bash
python3 src/scrape_rip.py --town <town-slug> --year <year>
```

### Options

| Flag | Default | Description |
|---|---|---|
| `--town` | _(empty)_ | Town slug, e.g. `rathfarnham`, `terenure`, `clontarf`. Leave empty to search the whole county. |
| `--county` | `dublin` | County name, e.g. `dublin`, `cork`, `galway` |
| `--year` | current year | Year to search. Sets `--from-date` and `--to-date` automatically. |
| `--from-date` | — | Start date in `YYYY-MM-DD` format (alternative to `--year`) |
| `--to-date` | — | End date in `YYYY-MM-DD` format (alternative to `--year`) |
| `--no-details` | off | Skip per-notice detail fetches. Faster but `date_of_death` may be missing. |
| `--delay` | `0.5` | Seconds between page requests |
| `--format` | `both` | Output format: `csv`, `json`, or `both` |
| `--output-csv` | `<town>_<year>.csv` | CSV output path |
| `--output-json` | `<town>_<year>.json` | JSON output path |

### Examples

```bash
# Rathfarnham, Dublin, all of 2025
python3 src/scrape_rip.py --town rathfarnham --year 2025

# Terenure, second half of 2025 only
python3 src/scrape_rip.py --town terenure --from-date 2025-06-01 --to-date 2025-12-31

# All of Dublin, 2025, skip detail fetches (faster)
python3 src/scrape_rip.py --county dublin --year 2025 --no-details

# Cork city, 2024
python3 src/scrape_rip.py --town cork-city --county cork --year 2024
```

### Output

Two files are created by default (e.g. `rathfarnham_2025.csv` and `rathfarnham_2025.json`).

Each record contains:

| Field | Example |
|---|---|
| `id` | `100001` |
| `firstname` | `Séamus (James)` |
| `surname` | `O'Reilly` |
| `nee` | `Ní Mhurchú` _(maiden name, if listed)_ |
| `town` | `Rathfarnham` |
| `county` | `Dublin` |
| `date_of_death` | `2025-03-15` |
| `year_of_death` | `2025` |
| `date_published` | `2025-03-16` |
| `url` | `https://www.rip.ie/death-notice/...` |

> **Note on `date_of_death`:** A small number of records will show `year_of_death` as the previous year (e.g. `2024`) if the person died in late December and the notice was published in January. Use `--year-offset 1` in `check_probate.py` to catch these.

---

## Script 2: check_probate.py

Takes the JSON output from `scrape_rip.py` and checks each person against the courts.ie probate register.

### Usage

```bash
python3 src/check_probate.py <input.json>
```

### Options

| Flag | Default | Description |
|---|---|---|
| `--output`, `-o` | `<input>_probate.json` | Output JSON file path |
| `--delay` | `0.8` | Seconds between requests to courts.ie |
| `--year-offset` | `0` | Also search ± N years around `year_of_death` |
| `--only-matches` | off | Only include persons with at least one grant found in the output |

### Examples

```bash
# Standard run — checks all 279 people, saves full results
python3 src/check_probate.py rathfarnham_2025.json

# Only save people where a grant was found (clean output)
python3 src/check_probate.py rathfarnham_2025.json --only-matches

# Also search the year before and after (catches edge cases)
python3 src/check_probate.py rathfarnham_2025.json --year-offset 1

# Custom output path
python3 src/check_probate.py rathfarnham_2025.json --output my_results.json
```

### Name handling

rip.ie names are not always in the format the probate register expects. The script handles this automatically:

| rip.ie name | Searches tried |
|---|---|
| `Séamus (James)` | `Séamus`, `James` |
| `Máire (Mary)` | `Máire`, `Mary` |
| `Pat (Patricia)` | `Pat`, `Patricia` |
| `Jo/Josie` | `Jo`, `Josie` |
| `Dr. Aoife` | `Aoife` |
| `Sr. Bríd` | `Bríd` |
| `BRADY` | `Brady` |
| `McAULIFFE` | `McAuliffe` |
| `O'SULLIVAN` | `O'Sullivan` |

### Output

A JSON file (e.g. `rathfarnham_2025_probate.json`) with the following structure:

```json
{
  "generated_at": "2025-12-30T12:00:00Z",
  "input_file": "rathfarnham_2025.json",
  "summary": {
    "persons_checked": 279,
    "total_searches": 310,
    "total_grants_found": 12,
    "persons_with_grants": 11
  },
  "results": [
    {
      "rip_ie": {
        "id": 100042,
        "firstname": "Patrick",
        "surname": "Flanagan",
        "nee": "",
        "town": "Rathfarnham",
        "county": "Dublin",
        "date_of_death": "2025-03-15",
        "year_of_death": "2025",
        "date_published": "2025-03-16",
        "url": "https://www.rip.ie/death-notice/patrick-flanagan-dublin-rathfarnham-100042"
      },
      "probate_searches": [
        {
          "search": {
            "firstname": "Patrick",
            "lastname": "Flanagan",
            "year": "2025",
            "url": "https://courts.ie/app/probate-register?firstname=Patrick&lastname=Flanagan&year=2025"
          },
          "grants_found": 1,
          "grants": [
            {
              "full_name": "Patrick Flanagan",
              "date_of_death": "15/03/2025",
              "grant_type": "Probate",
              "address": "14 Oakwood Avenue, Rathfarnham, Co. Dublin",
              "case_ref": "2025 PO 9999",
              "date_issued": "12/09/2025",
              "grantees": ["Anne Flanagan"]
            }
          ]
        }
      ],
      "probate_found": true
    }
  ]
}
```

The top-level `probate_found` flag on each result makes it easy to filter.

---

## Typical workflow

```bash
# Step 1 — scrape death notices for your area and year
python3 src/scrape_rip.py --town rathfarnham --year 2025

# Step 2 — check probate status for all of them
python3 src/check_probate.py rathfarnham_2025.json

# Step 3 — review results
#   Open rathfarnham_2025_probate.json and look for entries where
#   probate_found = true. Cross-reference the address in the grant
#   with the property you are buying.

# Or: get a clean list of just the matches
python3 src/check_probate.py rathfarnham_2025.json --only-matches --output matches_only.json
```

---

## Disclaimer

- This tool automates searches of publicly accessible websites. Check the terms of service of [rip.ie](https://rip.ie) before running `scrape_rip.py`.
- Output files may contain personal data about living individuals (grantees, addresses). These files are excluded from version control by `.gitignore` — **do not commit them**.
- This tool is intended for legitimate use only (e.g. verifying probate status when purchasing a property). The authors accept no liability for misuse.

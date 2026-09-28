"""
Fetch HDC summary tables (s_*) from the MOPH Open Data API into a local SQLite DB,
keeping only rows for the facilities / subdistrict we care about, plus district- and
province-wide sums for benchmarking. Rebuilds the static site (build_site.py) afterwards.

    POST https://opendata.moph.go.th/api/report_data
    {"tableName": "s_dm_screen", "year": "2568", "province": "40", "type": "json", "limit": N}

API facts (verified 2026-09-26/28):
  - No auth needed, but strict throttling: back-to-back calls get HTTP 429
    ("ThrottlerException: Too Many Requests"). ~20s between calls is safe.
  - Without "limit" the response is silently truncated at 1000 rows.
  - Success status is 201. Payload: {"data": [ {hospcode, areacode, date_com, b_year, ...}, ... ]}
  - Unknown table -> 404 {"message": "ไม่พบตาราง ..."}.
  - At most 10,000 rows per call whatever "limit" says; the payload carries "total", and
    "offset" pages through the rest (verified: s_person_pyramid 2568 has 48,156 rows).
  - Returns the whole province; we filter locally.

Report catalog (all HDC standard reports, ~760) comes from the HDC APIs:
  api-hdc     /v1/lookup/standard/catalog              categories + subcatalogs
  api-hdc     /v1/lookup/standard/report?subcatalogId  reports per subcatalog
  api-center  /v1/report-public/info?reportCode        source_table, byear_list
  api-center  /v1/report-public/detail?reportCode      header / column / SQL definition
Definitions don't vary by year, so one detail call per report; refreshed every CATALOG_DAYS.

Refresh strategy (a full sweep is ~3,000 calls ≈ 17h, far longer than the run interval),
in priority order, each run stopping after MAX_RUN_MINUTES of wall-clock time:
  1. (table, year) never fetched, or last attempt failed
  2. current fiscal year older than CURRENT_STALE_HOURS (HDC reprocesses daily)
  3. rows fetched before benchmark sums existed (AGG_SINCE)
  4. past fiscal years older than PAST_STALE_DAYS
Tables Open Data doesn't publish (404) are retried after UNAVAILABLE_DAYS.

Usage:
  python3 fetch_opendata.py                    # normal incremental run
  python3 fetch_opendata.py --sync-catalog     # force catalog refresh first
  python3 fetch_opendata.py --tables s_dm_screen --years 2568   # ad-hoc
"""
import argparse
import csv
import datetime as dt
import glob
import json
import os
import re
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
# Lives outside ~/Desktop on purpose: macOS TCC blocks launchd jobs from reading Desktop.
# The legacy catalog (cache/, report_meta.csv) in the Desktop project is only re-read when
# accessible (manual terminal runs); the full catalog now comes from the HDC API.
BASE = os.path.expanduser("~/Desktop/hdc_5y")
CACHE_DIR = os.path.join(BASE, "cache")
REPORT_META = os.path.join(BASE, "report_meta.csv")
DB_PATH = os.path.join(HERE, "opendata.sqlite")
LOCK_PATH = os.path.join(HERE, "fetch.lock")

API_URL = "https://opendata.moph.go.th/api/report_data"
HDC_API = "https://api-hdc.moph.go.th/v1"
HDC_CENTER_API = "https://api-center-hdc.moph.go.th/v1"
PROVINCE = "40"                       # ขอนแก่น
DISTRICT = "4002"                     # อ.บ้านฝาง
HOSPCODES = {"13896", "04276"}        # รพ.สต.บ้านแดง, รพ.สต.บ้านเหล่า
AREACODE_PREFIX = "400204"            # ต.บ้านเหล่า อ.บ้านฝาง
PRIMARY_HOSPCODE = "13896"            # used to classify table granularity
AGG_HOSPCODES = {"*AMP": DISTRICT, "*PROV": PROVINCE}   # pseudo rows: column sums
FIRST_YEAR = 2565
ROW_LIMIT = 10000

DELAY_SECONDS = 20
MAX_RETRIES = 5
CURRENT_STALE_HOURS = 20
PAST_STALE_DAYS = 30
UNAVAILABLE_DAYS = 14
CATALOG_DAYS = 7
AGG_SINCE = "2026-09-28T22:27:00"       # rows before this lack paging / hospital-located sums
PAGE_CAP = 10000                         # API's hard per-call maximum
MAX_RUN_MINUTES = 225                 # scheduled every 240 min

try:  # python.org builds on macOS ship without a CA bundle
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()

META_COLS = {"id", "hospcode", "areacode", "flag_sent", "date_com", "b_year", "ip"}


def log(msg):
    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


def current_fiscal_year_be(today=None):
    today = today or dt.date.today()
    return today.year + 543 + (1 if today.month >= 10 else 0)


# ---------------------------------------------------------------- db

SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog (
    report_code TEXT PRIMARY KEY,
    table_name  TEXT NOT NULL,
    label       TEXT,
    category    TEXT,
    subcatalog  TEXT,
    xlsx_file   TEXT
);
-- full HDC standard-report catalog with the definition needed to render each report
CREATE TABLE IF NOT EXISTS report_def (
    report_code TEXT PRIMARY KEY,
    table_name  TEXT,
    label       TEXT,
    category    TEXT,
    subcatalog  TEXT,
    years_json  TEXT,      -- byear_list from report-public/info
    def_json    TEXT,      -- {table, query, notice, rate} from report-public/detail
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fetch_state (
    table_name     TEXT NOT NULL,
    year           INTEGER NOT NULL,
    status         TEXT NOT NULL,       -- ok | error | unavailable
    http_code      INTEGER,
    n_rows_province INTEGER,
    n_rows_kept    INTEGER,
    fetched_at     TEXT NOT NULL,
    message        TEXT,
    PRIMARY KEY (table_name, year)
);
-- one row per API row kept; value columns vary per table so they stay as JSON.
-- hospcode '*AMP' / '*PROV' rows hold column sums for the district / province.
CREATE TABLE IF NOT EXISTS rows (
    table_name TEXT NOT NULL,
    year       INTEGER NOT NULL,
    hospcode   TEXT,
    areacode   TEXT,
    date_com   TEXT,
    data_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rows_tbl ON rows (table_name, year);
-- where each facility is located (from s_persontype, one row per facility at its own areacode);
-- HDC's area views filter by facility location, not by residents' areacode
CREATE TABLE IF NOT EXISTS hospital_area (
    hospcode TEXT PRIMARY KEY,
    areacode TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rows_area ON rows (areacode);
-- village: PRIMARY_HOSPCODE has rows for >1 areacode (counted by residence)
-- facility: a single row per hospital (areacode = hospital location, NOT a village filter)
-- none: no rows for PRIMARY_HOSPCODE in any fetched year
CREATE VIEW IF NOT EXISTS table_granularity AS
SELECT s.table_name,
       CASE WHEN MAX(n_area) > 1 THEN 'village'
            WHEN MAX(n_area) = 1 THEN 'facility'
            ELSE 'none' END AS granularity
FROM (SELECT DISTINCT table_name FROM fetch_state WHERE status = 'ok') s
LEFT JOIN (
    SELECT table_name, year, COUNT(DISTINCT areacode) AS n_area
    FROM rows WHERE hospcode = '%s' GROUP BY table_name, year
) r ON r.table_name = s.table_name
GROUP BY s.table_name;
""" % PRIMARY_HOSPCODE


def open_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    if "subcatalog_id" not in [r[1] for r in con.execute("PRAGMA table_info(report_def)")]:
        con.execute("ALTER TABLE report_def ADD COLUMN subcatalog_id TEXT")
    return con


# ---------------------------------------------------------------- catalog

def http_json(url, retries=3):
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60, context=SSL_CTX) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))


def first_row(payload):
    rows = (payload or {}).get("rows")
    if isinstance(rows, list):
        return rows[0] if rows else {}
    return rows or {}


def sync_catalog(con, force=False):
    """Refresh report_def from the HDC APIs (every CATALOG_DAYS, or when forced)."""
    last = con.execute("SELECT MAX(updated_at) FROM report_def").fetchone()[0]
    if not force and last and last > (dt.datetime.now() - dt.timedelta(days=CATALOG_DAYS)).isoformat():
        return
    log("syncing report catalog from HDC API")
    listing = {}
    for cat in http_json(f"{HDC_API}/lookup/standard/catalog")["rows"]:
        for sub in cat.get("sub_menu") or []:
            for r in http_json(f"{HDC_API}/lookup/standard/report?subcatalogId={sub['code']}").get("rows") or []:
                if r.get("active", True):
                    listing.setdefault(r["report_code"], {
                        "label": (r.get("report_name") or r.get("title_name") or "").strip(),
                        "category": cat["name"].strip(),
                        "subcatalog": sub["name"].strip(),
                        "subcatalog_id": sub["code"],
                    })
            time.sleep(0.3)
    log(f"  {len(listing)} active reports; fetching definitions")
    n_ok = 0
    for i, (code, meta) in enumerate(listing.items(), 1):
        try:
            info = first_row(http_json(f"{HDC_CENTER_API}/report-public/info?reportCode={code}&subCatalogId="))
            detail = first_row(http_json(f"{HDC_CENTER_API}/report-public/detail?reportCode={code}&byear={FIRST_YEAR}"))
        except Exception as e:  # keep the previous definition, try again next sync
            log(f"  {code}: definition fetch failed ({e})")
            continue
        table = info.get("source_table")
        if not table:
            m = re.search(r"CREATE TABLE IF NOT EXISTS\s+(s_\w+)", detail.get("s_sql") or "", re.I)
            table = m.group(1) if m else None
        definition = {
            "table": (detail.get("table") or [None])[0],
            "query": (detail.get("query") or [None])[0],
            "notice": detail.get("notice") or "",
            "rate": detail.get("rate") or "",
        }
        con.execute(
            "INSERT OR REPLACE INTO report_def VALUES (?,?,?,?,?,?,?,?)",
            (code, table, meta["label"], meta["category"], meta["subcatalog"],
             json.dumps(info.get("byear_list") or []), json.dumps(definition, ensure_ascii=False), now_iso()),
        )
        con.execute("UPDATE report_def SET subcatalog_id=? WHERE report_code=?", (meta["subcatalog_id"], code))
        n_ok += 1
        if i % 50 == 0:
            con.commit()
            log(f"  {i}/{len(listing)} definitions")
        time.sleep(0.3)
    con.commit()
    log(f"catalog synced: {n_ok}/{len(listing)} definitions stored")


def build_legacy_catalog(con):
    """Old cache-based catalog, still used by the Streamlit dashboard. Needs Desktop access."""
    meta = {}
    with open(REPORT_META, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            meta.setdefault(r["report_code"], r)
    rows = []
    for path in glob.glob(os.path.join(CACHE_DIR, "*.json")):
        with open(path, encoding="utf-8") as f:
            d = json.load(f).get("rows")
        if not isinstance(d, dict) or not d.get("s_sql"):
            continue
        m = re.search(r"CREATE TABLE IF NOT EXISTS\s+(s_\w+)", d["s_sql"], re.I)
        if m:
            mr = meta.get(d["report_code"], {})
            rows.append((d["report_code"], m.group(1), mr.get("label") or d.get("main_report_name") or "",
                         mr.get("category", ""), mr.get("subcatalog", ""), mr.get("xlsx_file", "")))
    con.executemany("INSERT OR REPLACE INTO catalog VALUES (?,?,?,?,?,?)", rows)
    con.commit()


def table_years(con, cur_year):
    """s_* table -> fiscal years worth fetching (union of its reports' byear_list)."""
    out = {}
    for table, years_json in con.execute("SELECT table_name, years_json FROM report_def WHERE table_name IS NOT NULL"):
        ys = {int(y) for y in json.loads(years_json or "[]") if str(y).isdigit()}
        ys = {y for y in ys if FIRST_YEAR <= y <= cur_year} or set(range(FIRST_YEAR, cur_year + 1))
        out.setdefault(table, set()).update(ys)
    for (table,) in con.execute("SELECT table_name FROM catalog"):
        out.setdefault(table, set(range(FIRST_YEAR, cur_year + 1)))
    return out


# ---------------------------------------------------------------- fetch

def post(table, year, offset=0):
    body = json.dumps({
        "tableName": table, "year": str(year), "province": PROVINCE,
        "type": "json", "limit": ROW_LIMIT, "offset": offset,
    }).encode()
    req = urllib.request.Request(API_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300, context=SSL_CTX) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def fetch_table_year(table, year):
    """All rows of one table/year, paging past the 10,000-row cap. Returns (http_code, rows | None, message)."""
    rows, total = [], None
    while True:
        code, page, total_now, message = fetch_page(table, year, len(rows))
        if page is None:
            return code, None, message
        rows.extend(page)
        total = total_now if total_now is not None else total
        if not page or total is None or len(rows) >= total or len(page) < PAGE_CAP:
            break
        time.sleep(DELAY_SECONDS)
    msg = f"got {len(rows)} of {total} rows" if total is not None and len(rows) < total else ""
    return code, rows, msg


def fetch_page(table, year, offset):
    """One page. Returns (http_code, rows | None, total | None, message). Retries on 429/5xx with backoff."""
    wait = DELAY_SECONDS
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            code, raw = post(table, year, offset)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            code, raw = None, str(e).encode()
        if code == 429 or code is None or code >= 500:
            log(f"  {table} {year}: {code or raw[:120].decode('utf-8', 'replace')}, retry {attempt}/{MAX_RETRIES} in {wait * 2}s")
            wait *= 2
            time.sleep(wait)
            continue
        if code not in (200, 201):
            return code, None, None, raw[:300].decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
            data = payload.get("data")
        except (ValueError, AttributeError):
            return code, None, None, "non-JSON response: " + raw[:200].decode("utf-8", "replace")
        if not isinstance(data, list):
            return code, None, None, "unexpected payload: " + raw[:200].decode("utf-8", "replace")
        total = payload.get("total")
        return code, data, (int(total) if str(total).isdigit() else None), ""
    return code, None, None, "gave up after retries: " + raw[:200].decode("utf-8", "replace")


def keep(row):
    return row.get("hospcode") in HOSPCODES or str(row.get("areacode") or "").startswith(AREACODE_PREFIX)


def column_sums(rows):
    """Sum every numeric value column; used for district / province benchmark rows."""
    sums = {}
    for r in rows:
        for k, v in r.items():
            if k in META_COLS or isinstance(v, bool):
                continue
            try:
                sums[k] = sums.get(k, 0) + float(v)
            except (TypeError, ValueError):
                pass
    return sums


def store(con, table, year, code, data, message):
    if data is None:
        # keep previously stored rows; only record the failure
        status = "unavailable" if code == 404 else "error"
        con.execute("INSERT OR REPLACE INTO fetch_state VALUES (?,?,?,?,?,?,?,?)",
                    (table, year, status, code, None, None, now_iso(), message))
        con.commit()
        return 0
    kept = [r for r in data if keep(r)]
    records = [
        (table, year, r.get("hospcode"), r.get("areacode"), r.get("date_com"),
         json.dumps({k: v for k, v in r.items() if k not in META_COLS}, ensure_ascii=False))
        for r in kept
    ]
    if table == "s_persontype":
        con.executemany("INSERT OR REPLACE INTO hospital_area VALUES (?, ?)",
                        [(r["hospcode"], str(r["areacode"])) for r in data if r.get("hospcode") and r.get("areacode")])
    located = dict(con.execute("SELECT hospcode, areacode FROM hospital_area"))
    date_com = max((str(r.get("date_com") or "") for r in data), default="")
    for pseudo, prefix in AGG_HOSPCODES.items():
        # like HDC: a district is the facilities located in it (fallback: residents' areacode)
        scope = [r for r in data if str(located.get(r.get("hospcode")) or r.get("areacode") or "").startswith(prefix)]
        if scope:
            records.append((table, year, pseudo, prefix, date_com, json.dumps(column_sums(scope))))
    con.execute("DELETE FROM rows WHERE table_name=? AND year=?", (table, year))
    con.executemany("INSERT INTO rows VALUES (?,?,?,?,?,?)", records)
    con.execute("INSERT OR REPLACE INTO fetch_state VALUES (?,?,?,?,?,?,?,?)",
                (table, year, "ok", code, len(data), len(kept), now_iso(), message))
    con.commit()
    return len(kept)


# ---------------------------------------------------------------- planning

def plan(con, wanted, cur_year, force):
    """[(table, year)] in priority order; see module docstring."""
    state = {(t, y): (s, at, n) for t, y, s, at, n in
             con.execute("SELECT table_name, year, status, fetched_at, n_rows_province FROM fetch_state")}
    now = dt.datetime.now()
    cur_stale = (now - dt.timedelta(hours=CURRENT_STALE_HOURS)).isoformat()
    past_stale = (now - dt.timedelta(days=PAST_STALE_DAYS)).isoformat()
    unavailable_retry = (now - dt.timedelta(days=UNAVAILABLE_DAYS)).isoformat()
    tiers = {1: [], 2: [], 3: [], 4: []}
    for table in sorted(wanted):
        for year in sorted(wanted[table], reverse=True):
            status, at, n_rows = state.get((table, year), (None, "", None))
            truncated = status == "ok" and n_rows is not None and n_rows % PAGE_CAP == 0 and n_rows > 0 and at < AGG_SINCE
            if truncated:
                tiers[1].insert(0, (table, year))
            elif status == "unavailable":
                if at < unavailable_retry:
                    tiers[4].append((table, year))
            elif status != "ok":
                tiers[1].append((table, year))
            elif year == cur_year and (force or at < cur_stale):
                tiers[2].append((table, year))
            elif at < AGG_SINCE:
                tiers[3].append((table, year))
            elif force or at < past_stale:
                tiers[4].append((table, year))
    return tiers[1] + tiers[2] + tiers[3] + tiers[4], {k: len(v) for k, v in tiers.items()}


def acquire_lock():
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            with open(LOCK_PATH) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            return False              # a previous run is still going
        except (ValueError, ProcessLookupError, PermissionError):
            os.remove(LOCK_PATH)      # stale lock from a crashed run
            return acquire_lock()
    with os.fdopen(fd, "w") as f:
        f.write(str(os.getpid()))
    return True


def rebuild_site():
    try:
        import build_site
        path = build_site.build()
        log(f"site rebuilt: {path}")
    except Exception as e:  # never let the site break data collection
        log(f"site rebuild failed: {e!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", nargs="*", help="limit to these s_* tables")
    ap.add_argument("--years", nargs="*", type=int, help="limit to these fiscal years (BE)")
    ap.add_argument("--force", action="store_true", help="refetch even if fresh")
    ap.add_argument("--sync-catalog", action="store_true", help="refresh report catalog now")
    ap.add_argument("--max-minutes", type=float, default=MAX_RUN_MINUTES)
    args = ap.parse_args()

    if not acquire_lock():
        log("another fetch is still running, exiting")
        return
    try:
        con = open_db()
        # wall clock, not time.monotonic(): on macOS monotonic time stops while asleep
        deadline = time.time() + args.max_minutes * 60
        try:
            build_legacy_catalog(con)
        except (PermissionError, FileNotFoundError):
            pass  # launchd can't read ~/Desktop; the stored catalog is fine
        try:
            sync_catalog(con, force=args.sync_catalog)
        except Exception as e:
            log(f"catalog sync failed, using stored catalog: {e!r}")

        cur_year = current_fiscal_year_be()
        wanted = table_years(con, cur_year)
        if args.tables:
            wanted = {t: wanted.get(t, set(range(FIRST_YEAR, cur_year + 1))) for t in args.tables}
        if args.years:
            wanted = {t: set(args.years) for t in wanted}
        todo, tiers = plan(con, wanted, cur_year, args.force)
        if not args.tables and not con.execute("SELECT 1 FROM hospital_area LIMIT 1").fetchone():
            todo = [("s_persontype", cur_year)] + [t for t in todo if t != ("s_persontype", cur_year)]
        log(f"{len(wanted)} tables, fiscal year now {cur_year}; {len(todo)} calls planned "
            f"(missing {tiers[1]}, current-year {tiers[2]}, add-benchmark {tiers[3]}, past/retry {tiers[4]})")

        ok = err = 0
        for i, (table, year) in enumerate(todo, 1):
            if time.time() > deadline:
                log(f"time budget reached, {len(todo) - i + 1} calls left for the next run")
                break
            code, data, message = fetch_table_year(table, year)
            n = store(con, table, year, code, data, message)
            if data is None:
                err += 1
                log(f"[{i}/{len(todo)}] {table} {year}: {'UNAVAILABLE' if code == 404 else 'ERROR'} {code} {message[:120]}")
            else:
                ok += 1
                log(f"[{i}/{len(todo)}] {table} {year}: {len(data)} rows, kept {n}" + (f" ({message})" if message else ""))
            if i % 60 == 0:
                rebuild_site()  # let the site fill in progressively during long runs
            if i < len(todo):
                time.sleep(DELAY_SECONDS)
        log(f"done: {ok} ok, {err} errors")
        rebuild_site()
    finally:
        os.remove(LOCK_PATH)


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    sys.exit(main())

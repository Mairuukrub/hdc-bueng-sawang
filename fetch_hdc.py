"""
Fetch report tables straight from the HDC website's own data API, i.e. exactly the numbers
https://hdc.moph.go.th/kkn/public/standard-report-detail/<code> shows, for:

  moo       village view filtered to อ.บ้านฝาง / ต.บ้านเหล่า      -> 14 village rows
  provider  facility view filtered to ต.บ้านเหล่า                -> 13896 บ้านแดง, 04276 บ้านเหล่า
  ampur     district view for จ.ขอนแก่น                         -> 26 district rows (4002 = บ้านฝาง)
  province  province view for เขต 7                              -> row 40 = ขอนแก่น
  other     category reports (pyramid, top-10 diseases ...) have no facility filter on HDC;
            fetched for ต.บ้านเหล่า / อ.บ้านฝาง / จ.ขอนแก่น

    GET https://api-hdc.moph.go.th/v1/reports/province/data/<report_code>?table_display=<view>
        &year=<BE>&zone=07&province_code=40&district_code=..&subdistrict_code=..&...
    header  domain: kkn          (selects the Khon Kaen HDC database; without it -> 502)

API facts (verified 2026-09-28): no login, ~0.2s per call, no throttling seen. Payload
rows[0] = {jsonc: column spec, data: [{a_code, a_name, <N columns as strings>}], datecom, label}.
F (formula) columns are not in `data`; the page computes them from jsonc, and so do we.

The report catalog (report_def: views, years, headers) is maintained by fetch_opendata.sync_catalog.

Refresh: (report, year) pairs never fetched first, then the current fiscal year when older
than CURRENT_STALE_HOURS, then past years older than PAST_STALE_DAYS. Rebuilds the site after.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch_opendata as base  # noqa: E402  (shared DB, catalog sync, lock, SSL, logging)

DATA_URL = "https://api-hdc.moph.go.th/v1/reports/province/data/{code}"
DOMAIN = "kkn"
ZONE, PROVINCE, DISTRICT, SUBDISTRICT = "07", "40", "4002", "400204"
FIRST_YEAR = base.FIRST_YEAR
DELAY_SECONDS = 0.3
CURRENT_STALE_HOURS = 20
PAST_STALE_DAYS = 30
MAX_RUN_MINUTES = 225
LOG = base.log

SCHEMA = """
CREATE TABLE IF NOT EXISTS hdc_rows (
    report_code TEXT NOT NULL,
    year        INTEGER NOT NULL,
    view        TEXT NOT NULL,     -- moo | provider | ampur | province | other_t | other_d | other_p
    a_code      TEXT NOT NULL,
    a_name      TEXT,
    data_json   TEXT NOT NULL,     -- the row exactly as HDC returned it
    PRIMARY KEY (report_code, year, view, a_code)
);
CREATE TABLE IF NOT EXISTS hdc_state (
    report_code TEXT NOT NULL,
    year        INTEGER NOT NULL,
    status      TEXT NOT NULL,     -- ok | error
    n_rows      INTEGER,
    datecom     TEXT,              -- HDC's processing date, as printed on the page
    jsonc_json  TEXT,              -- HDC's column spec for this report/year
    fetched_at  TEXT NOT NULL,
    message     TEXT,
    PRIMARY KEY (report_code, year)
);
"""

# (view stored as, table_display, district, subdistrict, keep row?)
UNIT_CALLS = [
    ("moo", "moo", DISTRICT, SUBDISTRICT, lambda r: True),
    ("provider", "provider", DISTRICT, SUBDISTRICT, lambda r: True),
    ("ampur", "ampur", "ALL", "ALL", lambda r: True),
    ("province", "province", "ALL", "ALL", lambda r: str(r.get("a_code")) == PROVINCE),
]
OTHER_CALLS = [
    ("other_t", "other", DISTRICT, SUBDISTRICT, lambda r: True),
    ("other_d", "other", DISTRICT, "ALL", lambda r: True),
    ("other_p", "other", "ALL", "ALL", lambda r: True),
]


class ViewError(Exception):
    """HDC answered with its own error for this view (e.g. its SQL fails): retrying won't help."""


def get_view(code, year, display, district, subdistrict):
    params = {
        "table_display": display, "year": year, "month": "ALL", "zone": ZONE, "province_code": PROVINCE,
        "district_code": district, "subdistrict_code": subdistrict, "department_code": "ALL",
        "organization_type": "ALL", "ministry": "ALL", "hospital": "ALL", "service_plan": "ALL",
        "jurisdiction_code": "ALL", "freeze_month": "ALL", "mental_code": "ALL", "mental_group_code": "ALL",
        "custom": "[]",
    }
    url = DATA_URL.format(code=code) + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"domain": DOMAIN, "Authorization": "Bearer null"})
    wait = 5
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120, context=base.SSL_CTX) as r:
                payload = json.load(r)
            break
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read())
            except ValueError:
                body = None
            if isinstance(body, dict) and body.get("ok") is False:
                raise ViewError(f"{display}: {str(body.get('message'))[:120]}")
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(wait)
                wait *= 3
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            if attempt == 3:
                raise
            time.sleep(wait)
            wait *= 3
    rows = payload.get("rows")
    if not payload.get("ok", True) or not isinstance(rows, list):
        raise RuntimeError(str(payload.get("message") or payload)[:200])
    return rows[0] if rows else {}


def fetch_report_year(con, code, year, views):
    calls = OTHER_CALLS if views == {"other"} else [c for c in UNIT_CALLS if c[1] in views]
    if not calls:
        return 0, None, None, "no view HDC can filter to our area"
    records, datecom, jsonc, failed = [], None, None, []
    for stored_as, display, district, subdistrict, keep in calls:
        try:
            block = get_view(code, year, display, district, subdistrict)
        except ViewError as e:  # this view is broken on HDC itself; keep the others
            failed.append(str(e))
            time.sleep(DELAY_SECONDS)
            continue
        datecom = datecom or block.get("datecom")
        jsonc = jsonc or block.get("jsonc")
        for r in block.get("data") or []:
            if keep(r):
                records.append((code, year, stored_as, str(r.get("a_code")), r.get("a_name"),
                                json.dumps(r, ensure_ascii=False)))
        time.sleep(DELAY_SECONDS)
    if failed and len(failed) == len(calls):
        raise ViewError("; ".join(failed))
    con.execute("DELETE FROM hdc_rows WHERE report_code=? AND year=?", (code, year))
    con.executemany("INSERT OR REPLACE INTO hdc_rows VALUES (?,?,?,?,?,?)", records)
    return len(records), datecom, jsonc, "; ".join(failed)


def plan(con, cur_year, force):
    state = {(c, y): (s, at) for c, y, s, at in con.execute("SELECT report_code, year, status, fetched_at FROM hdc_state")}
    now = dt.datetime.now()
    cur_stale = (now - dt.timedelta(hours=CURRENT_STALE_HOURS)).isoformat()
    past_stale = (now - dt.timedelta(days=PAST_STALE_DAYS)).isoformat()
    missing, current, past = [], [], []
    for code, years_json, def_json in con.execute("SELECT report_code, years_json, def_json FROM report_def"):
        definition = json.loads(def_json or "{}")
        views = {k for k, v in (definition.get("query") or {}).items() if isinstance(v, dict) and (v.get("query") or "").strip()}
        years = {int(y) for y in json.loads(years_json or "[]") if str(y).isdigit()}
        if years and cur_year - 1 in years:  # HDC lists a new fiscal year only once it has data
            years.add(cur_year)
        years = sorted((y for y in years if FIRST_YEAR <= y <= cur_year), reverse=True) or list(range(cur_year, FIRST_YEAR - 1, -1))
        for y in years:
            status, at = state.get((code, y), (None, ""))
            item = (code, y, views)
            if status != "ok":
                missing.append(item)
            elif y == cur_year and (force or at < cur_stale):
                current.append(item)
            elif force or at < past_stale:
                past.append(item)
    return missing + current + past, (len(missing), len(current), len(past))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports", nargs="*", help="limit to these report codes")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--sync-catalog", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=MAX_RUN_MINUTES)
    args = ap.parse_args()

    if not base.acquire_lock():
        LOG("another fetch is still running, exiting")
        return
    try:
        con = base.open_db()
        con.executescript(SCHEMA)
        deadline = time.time() + args.max_minutes * 60
        try:
            base.sync_catalog(con, force=args.sync_catalog)
        except Exception as e:
            LOG(f"catalog sync failed, using stored catalog: {e!r}")
        cur_year = base.current_fiscal_year_be()
        todo, (n_missing, n_cur, n_past) = plan(con, cur_year, args.force)
        if args.reports:
            todo = [t for t in todo if t[0] in set(args.reports)]
        LOG(f"HDC data API: {len(todo)} report-years planned (missing {n_missing}, current-year {n_cur}, past {n_past})")
        ok = err = 0
        for i, (code, year, views) in enumerate(todo, 1):
            if time.time() > deadline:
                LOG(f"time budget reached, {len(todo) - i + 1} left for the next run")
                break
            try:
                n, datecom, jsonc, message = fetch_report_year(con, code, year, views)
                con.execute("INSERT OR REPLACE INTO hdc_state VALUES (?,?,?,?,?,?,?,?)",
                            (code, year, "ok", n, datecom, json.dumps(jsonc, ensure_ascii=False) if jsonc else None,
                             base.now_iso(), message))
                ok += 1
                if i % 25 == 0 or i == len(todo):
                    LOG(f"[{i}/{len(todo)}] {code} {year}: {n} rows ({datecom})")
            except Exception as e:
                con.execute("INSERT OR REPLACE INTO hdc_state VALUES (?,?,?,?,?,?,?,?)",
                            (code, year, "error", None, None, None, base.now_iso(), repr(e)[:300]))
                err += 1
                LOG(f"[{i}/{len(todo)}] {code} {year}: ERROR {e!r}"[:300])
            con.commit()
            if i % 400 == 0:
                base.rebuild_site()
        LOG(f"HDC data API done: {ok} ok, {err} errors")
        base.rebuild_site()
    finally:
        os.remove(base.LOCK_PATH)


if __name__ == "__main__":
    sys.exit(main())

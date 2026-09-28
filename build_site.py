"""
Build site/data.js (and export/*.csv) for the บ้านบึงสว่าง / รพ.สต.บ้านแดง site from the
rows fetch_hdc.py copied out of the HDC website's own data API (table hdc_rows).

Numbers are HDC's as-is. Like the HDC page, only the formula (F) columns are computed here,
from the report's jsonc formulas; sums (province from districts, category totals) add up the
N columns first and then apply the formulas, the same way HDC's "รวม" row works.

Scopes:
  v  = บ้านบึงสว่าง (village view, ต.บ้านเหล่า, row 40020411)   vil = all villages of บ้านแดง
  f  = รพ.สต.บ้านแดง (facility view, row 13896)
  t  = ต.บ้านเหล่า (category reports: HDC can't filter those below the subdistrict)
  d  = อ.บ้านฝาง (district view row 4002)      p = จ.ขอนแก่น (province view row 40)
"""
import csv
import datetime as dt
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hdc_render  # noqa: E402

DB_PATH = os.path.join(HERE, "opendata.sqlite")
SITE_DIR = os.path.join(HERE, "site")
EXPORT_DIR = os.path.join(HERE, "export")

FACILITY = "13896"
VILLAGE = "40020411"
DISTRICT = "4002"
PROVINCE = "40"
# รพ.สต.บ้านแดง's villages (ต.บ้านเหล่า); names as HDC's village view prints them
VILLAGES = {
    "40020401": "หนองบัว", "40020406": "แดง", "40020409": "โนนเขวา", "40020410": "ค้อ",
    "40020411": "บึงสว่าง", "40020412": "แดง", "40020414": "ใหม่สุขสันติ",
}
SCOPE_NAMES = {"v": "บ้านบึงสว่าง ม.11", "f": "รพ.สต.บ้านแดง", "t": "ต.บ้านเหล่า", "d": "อ.บ้านฝาง", "p": "จ.ขอนแก่น"}
HDC_REPORT_URL = "https://hdc.moph.go.th/kkn/public/standard-report-detail/{code}?subcatalogId={sid}"


def current_fiscal_year_be(today=None):
    today = today or dt.date.today()
    return today.year + 543 + (1 if today.month >= 10 else 0)


def clean(v):
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return None
    v = float(v)
    return int(v) if v.is_integer() else v


def report_frame(rows):
    """hdc_rows of one report -> DataFrame (_year, _view, _code, _name, <numeric columns>)."""
    recs = []
    for year, view, a_code, a_name, data_json in rows:
        d = json.loads(data_json)
        rec = {k.lower(): v for k, v in d.items() if k not in ("a_code", "a_name")}
        rec.update(_year=year, _view=view, _code=str(a_code), _name=a_name)
        recs.append(rec)
    df = pd.DataFrame(recs)
    for c in df.columns:
        if not c.startswith("_"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def with_sums(df, spec, jsonc_types):
    """Append the totals HDC would show: province from districts (when the report has no
    province view) and per-area category totals for 'other' reports."""
    n_cols = [name for _, kind, name, _, _ in spec if kind == "count" and name in df.columns]
    summable = [c for c in n_cols if str(jsonc_types.get(c, "SUM")).upper() == "SUM"]
    extra = []
    for year, part in df.groupby("_year"):
        views = set(part["_view"])
        if "province" not in views and "ampur" in views:
            s = part[part["_view"] == "ampur"][summable].sum(min_count=1)
            extra.append({**s.to_dict(), "_year": year, "_view": "province", "_code": PROVINCE, "_name": "รวม"})
        for view in ("other_t", "other_d", "other_p"):
            sub = part[part["_view"] == view]
            if not sub.empty:
                s = sub[summable].sum(min_count=1)
                extra.append({**s.to_dict(), "_year": year, "_view": view + "_total", "_code": "*", "_name": "รวม"})
    return pd.concat([df, pd.DataFrame(extra)], ignore_index=True) if extra else df


def compute(df, spec, rate):
    """One column per spec entry: N straight from HDC, F from the report's formula."""
    frame = df[[c for c in df.columns if not c.startswith("_")]].fillna(0).copy()
    frame["rate"] = rate
    out = {}
    for j, (_, kind, name, formula, digits) in enumerate(spec):
        if kind == "rate":
            try:
                val = hdc_render._eval(formula, frame) if formula else np.nan
            except Exception:
                val = np.nan
        else:
            val = df[name] if name in df.columns else np.nan
        out[j] = pd.Series(val, index=df.index).replace([np.inf, -np.inf], np.nan).round(digits)
    return pd.DataFrame(out, index=df.index)


def build_report(definition, rows, jsonc):
    df = report_frame(rows)
    if df.empty:
        return None
    table = dict(definition.get("table") or {})
    if jsonc:
        table["json_column"] = jsonc
    spec = hdc_render.column_spec({"table": table})
    if not spec:
        return None
    jsonc_types = {str(c.get("column_name", "")).lower(): c.get("sum_type", "SUM") for c in (jsonc or [])}
    df = with_sums(df, spec, jsonc_types)
    vals = compute(df, spec, float(definition.get("rate") or 100))

    def pick(view, a_code):
        m = (df["_view"] == view) & (df["_code"] == a_code)
        return {str(y): [clean(v) for v in vals.loc[i].tolist()] for i, y in zip(df.index[m], df.loc[m, "_year"])}

    group = bool(df["_view"].str.startswith("other").any())
    if group:
        series = {"t": pick("other_t_total", "*"), "d": pick("other_d_total", "*"), "p": pick("other_p_total", "*")}
    else:
        series = {"v": pick("moo", VILLAGE), "f": pick("provider", FACILITY),
                  "d": pick("ampur", DISTRICT), "p": pick("province", PROVINCE)}
    series = {k: v for k, v in series.items() if v}

    ours = [row for s in ("v", "f", "t") for row in series.get(s, {}).values()]
    keep = [j for j in range(len(spec)) if any(row[j] is not None for row in ours)]
    if not keep:
        return None
    series = {s: {y: [row[j] for j in keep] for y, row in by_year.items()} for s, by_year in series.items()}
    cols = [{"l": " › ".join(spec[j][0]), "k": spec[j][1]} for j in keep]
    main = series.get("v") or series.get("f") or series.get("t") or {}
    primary = next((i for i, c in enumerate(cols) if c["k"] == "rate" and any(v[i] is not None for v in main.values())), None)
    if primary is None:  # no rate: category tables end with their total ("รวม"), others lead with it
        primary = len(cols) - 1 if group else 0

    body = {"cols": cols, "p": primary, "hid": len(spec) - len(keep), "a": series,
            "y": sorted({int(y) for s in ("v", "f", "t") for y in series.get(s, {})})}
    if group:
        grp = {}
        for scope, view in (("t", "other_t"), ("d", "other_d"), ("p", "other_p")):
            for i in df.index[df["_view"] == view]:
                row = [clean(v) for v in vals.loc[i].tolist()]
                grp.setdefault(scope, {}).setdefault(str(df.at[i, "_year"]), []).append(
                    [str(df.at[i, "_name"] or df.at[i, "_code"]), [row[j] for j in keep]])
        body["grp"] = grp
        body["st"] = "group"
    else:
        vil = {}
        for i in df.index[(df["_view"] == "moo") & df["_code"].isin(VILLAGES)]:
            row = [clean(v) for v in vals.loc[i].tolist()]
            vil.setdefault(str(df.at[i, "_year"]), {})[df.at[i, "_code"]] = [row[j] for j in keep]
        body["vil"] = vil
        body["st"] = "village" if "v" in series else "facility"
    return body


def build():
    con = sqlite3.connect(DB_PATH)
    defs = pd.read_sql("SELECT * FROM report_def", con)
    has_state = con.execute("SELECT 1 FROM sqlite_master WHERE name='hdc_state'").fetchone()
    state, rows, last_fetch = {}, {}, None
    if has_state:
        for code, year, status, datecom, jsonc_json in con.execute(
                "SELECT report_code, year, status, datecom, jsonc_json FROM hdc_state"):
            state.setdefault(code, []).append((year, status, datecom, jsonc_json))
        for code, year, view, a_code, a_name, data_json in con.execute(
                "SELECT report_code, year, view, a_code, a_name, data_json FROM hdc_rows"):
            rows.setdefault(code, []).append((year, view, a_code, a_name, data_json))
        last_fetch = con.execute("SELECT MAX(fetched_at) FROM hdc_state WHERE status='ok'").fetchone()[0]
    con.close()

    reports = []
    for _, d in defs.sort_values(["category", "subcatalog", "label"]).iterrows():
        code = d["report_code"]
        entry = {"c": code, "t": hdc_render.strip_html(d["label"]), "cat": hdc_render.strip_html(d["category"]),
                 "sub": hdc_render.strip_html(d["subcatalog"])}
        if d.get("subcatalog_id"):
            entry["sid"] = d["subcatalog_id"]
        definition = json.loads(d["def_json"] or "{}")
        notice = hdc_render.strip_html(definition.get("notice") or "")
        if notice:
            entry["n"] = notice
        views = set(hdc_render.views(definition))
        ok = sorted((s for s in state.get(code, []) if s[1] == "ok"), key=lambda s: s[0], reverse=True)
        if not ok:
            entry["st"] = "pending"
        elif not views & {"moo", "provider", "other"}:
            entry["st"] = "province"   # HDC only offers province/zone views for this report
        else:
            jsonc = next((json.loads(s[3]) for s in ok if s[3]), None)
            try:
                body = build_report(definition, rows.get(code, []), jsonc)
            except Exception as e:  # one odd definition shouldn't sink the site
                print(f"render failed for {code}: {e!r}", file=sys.stderr)
                body = None
            if body:
                entry.update(body)
                entry["dc"] = ok[0][2] or ""
            else:
                entry["st"] = "none"
        reports.append(entry)

    counts = {}
    for r in reports:
        counts[r["st"]] = counts.get(r["st"], 0) + 1
    data = {
        "built": dt.datetime.now().isoformat(timespec="minutes"),
        "fetched": last_fetch,
        "fy": current_fiscal_year_be(),
        "counts": counts,
        "hdcUrl": HDC_REPORT_URL,
        "villages": [{"code": c, "moo": int(c[-2:]), "name": n} for c, n in VILLAGES.items()],
        "village": VILLAGE,
        "reports": reports,
    }
    os.makedirs(SITE_DIR, exist_ok=True)
    path = os.path.join(SITE_DIR, "data.js")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("window.HDC_DATA = ")
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    os.replace(tmp, path)
    write_export(reports)
    return path


def write_export(reports):
    """Human-readable long table of everything on the site, for Excel / Numbers."""
    os.makedirs(EXPORT_DIR, exist_ok=True)
    path = os.path.join(EXPORT_DIR, "ข้อมูลบ้านบึงสว่าง_รพ.สต.บ้านแดง.csv")
    tmp = path + ".tmp"
    kinds = {"village": "แยกรายหมู่บ้าน", "facility": "ระดับ รพ.สต.", "group": "แยกตามกลุ่ม"}
    with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["หมวด", "หมวดย่อย", "รายงาน", "ประเภท", "พื้นที่", "กลุ่ม", "ปีงบ", "ตัวชี้วัด", "ชนิดค่า", "ค่า", "รหัสรายงาน"])

        def put(r, area, group, year, vals):
            for c, v in zip(r["cols"], vals):
                if v is not None:
                    w.writerow([r["cat"], r["sub"], r["t"], kinds.get(r["st"], r["st"]), area, group, year,
                                c["l"], "อัตรา" if c["k"] == "rate" else "จำนวน", v, r["c"]])

        for r in reports:
            if "cols" not in r:
                continue
            for scope, by_year in r.get("a", {}).items():
                for year, vals in sorted(by_year.items()):
                    put(r, SCOPE_NAMES[scope], "", year, vals)
            for scope, by_year in r.get("grp", {}).items():
                for year, items in sorted(by_year.items()):
                    for label, vals in items:
                        put(r, SCOPE_NAMES[scope], label, year, vals)
            for year, by_village in sorted(r.get("vil", {}).items()):
                for code, vals in by_village.items():
                    if code != VILLAGE:  # บึงสว่าง is already there as scope v
                        put(r, f"ม.{int(code[-2:])} {VILLAGES[code]}", "", year, vals)
    os.replace(tmp, path)
    return path


if __name__ == "__main__":
    print(build())

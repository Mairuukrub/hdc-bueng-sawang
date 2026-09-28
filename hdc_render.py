"""
Turn raw s_* rows into the Thai-labelled table HDC shows, from a report definition
(report-public/detail): table.json_header (multi-row header), table.json_column
(N = SELECT alias, F = formula over aliases) and query[view].query (HDC's SQL, whose
SELECT list maps raw columns -> aliases).

We re-evaluate that SELECT list in pandas (sum(...) per group, then arithmetic) and then
the F formulas. Columns whose SQL uses constructs we don't emulate (IF, FILTER, COUNT,
sub-queries) come back empty rather than wrong.
"""
import html
import re

import numpy as np
import pandas as pd


def strip_html(text):
    """HDC labels/notices carry markup such as <font color=red>; drop tags, keep "LDL < 100"."""
    text = html.unescape(text or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</?[a-zA-Z][^<>]*>", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def flatten_header(json_header):
    """Multi-row rowspan/colspan header -> one label per bottom-level column."""
    grid = {}
    for r, row in enumerate(json_header or []):
        c = 0
        for cell in row:
            while (r, c) in grid:
                c += 1
            # HDC writes span 0 for "no span", i.e. 1
            rowspan = max(1, int(cell.get("rowspan") or 1))
            colspan = max(1, int(cell.get("colspan") or 1))
            name = " ".join(strip_html(str(cell.get("name", ""))).split())
            for dr in range(rowspan):
                for dc in range(colspan):
                    grid[(r + dr, c + dc)] = name
            c += colspan
    ncols = max((c for _, c in grid), default=-1) + 1
    labels = []
    for c in range(ncols):
        parts = []
        for r in range(len(json_header or [])):
            name = grid.get((r, c))
            if name and (not parts or parts[-1] != name):
                parts.append(name)
        labels.append(parts)
    return labels


def _split_top_level(s, sep=","):
    out, depth, cur, quote = [], 0, [], None
    for ch in s:
        if quote:
            quote = None if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == sep and depth == 0:
            out.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    out.append("".join(cur))
    return out


def _find_calls(expr, fname):
    """Yield (start, end, inner) for each fname( ... ) with balanced parens."""
    for m in re.finditer(rf"\b{fname}\s*\(", expr, re.I):
        depth, i = 1, m.end()
        while i < len(expr) and depth:
            depth += {"(": 1, ")": -1}.get(expr[i], 0)
            i += 1
        yield m.start(), i, expr[m.end():i - 1]


def select_items(sql):
    """[(alias, expr)] from the top-level SELECT list of HDC's report SQL."""
    m = re.search(r"\bselect\b(.*?)\bfrom\b\s*\{\{S_TABLE\}\}", sql or "", re.I | re.S)
    if not m:
        m = re.search(r"\bselect\b(.*?)\bfrom\b", sql or "", re.I | re.S)
    if not m:
        return []
    items = []
    for part in _split_top_level(m.group(1)):
        am = re.match(r"(.*)\bas\s+[`\"]?(\w+)[`\"]?\s*$", part.strip(), re.I | re.S)
        if am:
            items.append((am.group(2).lower(), am.group(1).strip()))
    return items


def _unwrap(expr, fname):
    """fname(a, ...) -> (a): IFNULL/COALESCE are no-ops after fillna(0); ROUND keeps precision."""
    while True:
        calls = list(_find_calls(expr, fname))
        if not calls:
            return expr
        start, end, inner = calls[-1]
        expr = expr[:start] + "(" + _split_top_level(inner)[0] + ")" + expr[end:]


def _case(expr):
    """CASE WHEN c1 THEN v1 [WHEN ...] [ELSE v] END -> arithmetic pandas.eval understands."""
    pat = re.compile(r"\bcase\s+((?:(?!\bcase\b).)+?)\bend\b", re.I | re.S)
    while True:
        m = pat.search(expr)
        if not m:
            return expr
        body = m.group(1)
        else_part = "0"
        em = re.search(r"\belse\b(.*)$", body, re.I | re.S)
        if em:
            else_part, body = em.group(1).strip(), body[:em.start()]
        arms = re.findall(r"\bwhen\b(.*?)\bthen\b(.*?)(?=\bwhen\b|$)", body, re.I | re.S)
        out = f"({else_part})"
        for cond, val in reversed(arms):
            out = f"(({cond.strip()}) * ({val.strip()}) + (~({cond.strip()})) * {out})"
        expr = expr[:m.start()] + out + expr[m.end():]


def _py(expr):
    """SQL-ish arithmetic -> pandas.eval syntax (column names like 1B0282 need backticks)."""
    expr = re.sub(r"\b[sp]\.", "", expr).replace("<>", "!=").lower()
    expr = re.sub(r'["`](\w+)["`]', r"\1", expr)  # "type1" / `type1` quoted identifiers
    for fname in ("ifnull", "coalesce", "round"):
        expr = _unwrap(expr, fname)
    expr = _case(expr)
    expr = re.sub(r"'(-?\d+(?:\.\d+)?)'", r"\1", expr)  # '1' -> 1 (codes are numeric in our rows)
    expr = re.sub(r"(?<![<>!=])=(?!=)", "==", expr)
    expr = re.sub(r"\bin\s*\(([^()]*)\)", r"in [\1]", expr)
    return re.sub(r"\b(\d+[a-z_]\w*)\b", r"`\1`", expr)


def _eval(expr, frame):
    return frame.eval(_py(expr), engine="python")


def compute_aliases(raw, group_col, sql):
    """Evaluate HDC's SELECT list over raw rows grouped by group_col."""
    raw = raw.fillna(0)
    groups = raw[group_col]
    agg = pd.DataFrame(index=pd.Index(sorted(groups.unique()), name=group_col))
    items = select_items(sql)
    if not items:  # no usable SQL: expose the raw numeric columns as-is
        for c in raw.columns:
            if c not in (group_col, "table_name", "year", "hospcode", "areacode", "date_com"):
                agg[c] = pd.to_numeric(raw[c], errors="coerce").groupby(groups).sum()
        return agg
    for alias, expr in items:
        if alias in ("a_code", "a_name", "areacode", "hoscode", "areamame"):
            continue
        try:
            expr = re.sub(r"\bcount\s*\(\s*(\*|1)\s*\)", "sum(1)", expr, flags=re.I)
            e, tmp = expr, {}
            for k, (s, t, inner) in enumerate(reversed(list(_find_calls(expr, "sum")))):
                col = f"__s{k}"
                val = _eval(inner, raw)
                if not isinstance(val, pd.Series):  # constant such as SUM(1)
                    val = pd.Series(val, index=raw.index)
                tmp[col] = val.groupby(groups).sum()
                e = e[:s] + col + e[t:]
            if not tmp and re.fullmatch(r"[\w.]+", expr):
                tmp["__s0"], e = raw[expr.split(".")[-1].lower()].groupby(groups).sum(), "__s0"
            agg[alias] = _eval(e, pd.DataFrame(tmp, index=agg.index)) if tmp else np.nan
        except Exception:
            agg[alias] = np.nan
    return agg.replace([np.inf, -np.inf], np.nan)


def column_spec(report_def):
    """[(label_parts, kind, column_name, formula, digits)] for displayed columns, in order."""
    tbl = report_def.get("table") or {}
    cols = tbl.get("json_column") or []
    labels = flatten_header(tbl.get("json_header") or [])
    # json_column is listed in header order; its "position" values are sometimes wrong
    # (duplicated / skipped), so pair by list index whenever the counts line up
    by_index = len(labels) == len(cols)
    if not by_index:
        cols = sorted(cols, key=lambda c: int(c.get("position", 0)))
    spec = []
    for i, c in enumerate(cols):
        pos = i if by_index else int(c.get("position", 0))
        ctype, name = c.get("type"), str(c.get("column_name", "")).lower()
        if ctype == "C" or c.get("is_show") in (0, "0"):
            continue
        label = labels[pos] if pos < len(labels) and labels[pos] else [name]
        formula = re.sub(r"#\{(\w+)\}", lambda m: m.group(1).lower(), c.get("formula") or "") if ctype == "F" else ""
        spec.append((label, "rate" if ctype == "F" else "count", name, formula, int(c.get("digit_number") or 0)))
    return spec


VIEW_ORDER = ("moo", "provider", "hospital", "other", "ampur", "province", "zone", "under")


def views(report_def):
    q = report_def.get("query") or {}
    return [k for k in VIEW_ORDER if isinstance(q.get(k), dict) and (q[k].get("query") or "").strip()]


def view_sql(report_def):
    vs = views(report_def)
    return report_def["query"][vs[0]]["query"] if vs else ""


def group_key(sql, columns):
    """For HDC 'other' views (rows = categories such as age group or ICD group):
    (raw column holding the category, raw column holding its name or None, LIMIT n or None)."""
    columns = set(columns)
    select = re.split(r"\bfrom\b", sql, maxsplit=1, flags=re.I)[0]
    items = dict(select_items(select + " FROM x"))

    def raw_col(expr):
        for alias, col in re.findall(r"\b(\w+)\.(\w+)", expr or ""):
            col = col.lower()
            if alias.lower() == "s" and col in columns:
                return col
            j = re.search(rf"\b{alias}\.{col}\s*=\s*s\.(\w+)|\bs\.(\w+)\s*=\s*{alias}\.{col}\b", sql, re.I)
            if j and (j.group(1) or j.group(2)).lower() in columns:
                return (j.group(1) or j.group(2)).lower()
            if col in columns:
                return col
        return None

    key = raw_col(items.get("a_code"))
    name = raw_col(items.get("a_name"))
    limit = re.search(r"\blimit\s+(\d+)\s*$", sql.strip(), re.I)
    return key, (name if name != key else None), (int(limit.group(1)) if limit else None)


def render(raw, report_def, group_col):
    """raw s_* rows -> (spec, DataFrame indexed by group, one column per spec entry)."""
    agg = compute_aliases(raw, group_col, view_sql(report_def))
    agg["rate"] = float(report_def.get("rate") or 100)
    spec = column_spec(report_def)
    if not spec:  # no header definition: show whatever we could compute
        spec = [([c], "count", c, "", 0) for c in agg.columns if c != "rate"]
    out = pd.DataFrame(index=agg.index)
    for j, (_, kind, name, formula, digits) in enumerate(spec):
        if kind == "rate":
            try:
                val = _eval(formula, agg) if formula else np.nan
            except Exception:
                val = np.nan
        else:
            val = agg[name] if name in agg.columns else np.nan
        out[j] = pd.Series(val, index=agg.index).replace([np.inf, -np.inf], np.nan).round(digits)
    return spec, out

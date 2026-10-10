(() => {
  "use strict";

  const D = window.HDC_DATA;
  const app = document.getElementById("app");
  const qInput = document.getElementById("q");
  const tip = document.getElementById("tip");

  const TH_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."];
  const AREA = { v: "บ้านบึงสว่าง (ม.11)", f: "รพ.สต.บ้านแดง" };
  const SCOPE_NAME = { v: "บ้านบึงสว่าง", f: "รพ.สต.บ้านแดง", t: "ต.บ้านเหล่า", d: "อ.บ้านฝาง", p: "จ.ขอนแก่น" };
  const BENCH_VAR = ["--bench-1", "--bench-2", "--bench-3"];
  const STATUS = {
    village: ["village", "แยกรายหมู่บ้าน"],
    facility: ["facility", "ระดับ รพ.สต."],
    group: ["group", "แยกตามกลุ่ม"],
    none: ["", "ไม่มีข้อมูลของพื้นที่นี้"],
    province: ["", "HDC มีเฉพาะระดับจังหวัด"],
    pending: ["", "รอดึงข้อมูล"],
    unavailable: ["", "Open Data ไม่เปิดข้อมูลนี้"],
    nodef: ["", "ไม่มีโครงสร้างรายงาน"],
  };
  const HAS_DATA = (s) => s === "village" || s === "facility" || s === "group";

  // typed word -> extra words that should also match
  const SYN = {
    "เบาหวาน": ["dm", "hba1c", "น้ำตาล"], dm: ["เบาหวาน"],
    "ความดัน": ["ht", "ความดันโลหิต"], ht: ["ความดัน"],
    "ปอด": ["copd", "ถุงลม", "หืด"], copd: ["ปอดอุดกั้น"],
    "ไต": ["ckd", "egfr"], ckd: ["ไต"],
    "หัวใจ": ["หลอดเลือด", "stemi", "cvd"],
    "มะเร็ง": ["cancer", "ca "],
    "วัคซีน": ["epi", "ภูมิคุ้มกัน"],
    "ซึมเศร้า": ["2q", "9q", "8q"], "สุขภาพจิต": ["2q", "9q", "จิต", "ซึมเศร้า"],
    "ฟัน": ["ทันต", "ช่องปาก", "dental"], "ทันตกรรม": ["ฟัน", "ช่องปาก"],
    "ผู้สูงอายุ": ["60 ปี", "adl", "ageing"],
    "เด็ก": ["ทารก", "แรกเกิด", "นักเรียน"],
    "แม่": ["ตั้งครรภ์", "anc", "คลอด"], "ตั้งครรภ์": ["anc", "ฝากครรภ์"],
    "ยาเสพติด": ["สารเสพติด", "บุหรี่", "สุรา", "แอลกอฮอล์"],
    "อ้วน": ["bmi", "ภาวะโภชนาการ"], "โภชนาการ": ["bmi", "อ้วน", "เตี้ย"],
  };
  const TOPICS = ["เบาหวาน", "ความดัน", "ผู้สูงอายุ", "แม่และเด็ก", "วัคซีน", "ฟัน", "สุขภาพจิต", "มะเร็ง", "ไต", "ประชากร"];
  // home-page tiles: first report whose title contains every word and has data
  const KEY = [
    ["คัดกรอง", "เบาหวาน"], ["คัดกรอง", "ความดัน"], ["ควบคุม", "น้ำตาล"], ["ควบคุม", "ความดัน"],
    ["ผู้สูงอายุ", "9 ด้าน"], ["ผู้สูงอายุ", "กิจวัตรประจำวัน"], ["คัดกรอง", "มะเร็งเต้านม"],
    ["คัดกรอง", "มะเร็งปากมดลูก"], ["วัคซีน"], ["ซึมเศร้า", "2Q"], ["ฝากครรภ์"], ["ทันต"],
  ];

  // ---------------------------------------------------------------- helpers
  function h(tag, attrs, ...kids) {
    const n = document.createElementNS(tag === "svg" || attrs?.svg ? "http://www.w3.org/2000/svg" : "http://www.w3.org/1999/xhtml", tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "svg" || v == null || v === false) continue;
      if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else if (k === "class") n.setAttribute("class", v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) {
      if (kid == null || kid === false) continue;
      n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return n;
  }
  const s = (tag, attrs, ...kids) => h(tag, { ...attrs, svg: true }, ...kids);
  const mount = (node, ...kids) => node.replaceChildren(...kids.flat().filter((k) => k != null && k !== false));
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  function fmt(v) {
    if (v == null || Number.isNaN(v)) return "–";
    return Number(v).toLocaleString("th-TH", { maximumFractionDigits: 2 });
  }
  function fmtDelta(v) {
    if (v == null) return "";
    const sign = v > 0 ? "▲ +" : v < 0 ? "▼ −" : "±";
    return sign + Math.abs(v).toLocaleString("th-TH", { maximumFractionDigits: 2 });
  }
  function fmtDateCom(dc) {  // "202609251912" (AD) -> "25 ก.ย. 2569 19:12"; HDC's own text passes through
    if (!dc) return "";
    if (!/^\d{8,}$/.test(dc)) return dc;
    const y = +dc.slice(0, 4) + 543, m = +dc.slice(4, 6), d = +dc.slice(6, 8);
    const t = dc.length >= 12 ? ` ${dc.slice(8, 10)}:${dc.slice(10, 12)} น.` : "";
    return `${d} ${TH_MONTHS[m - 1]} ${y}${t}`;
  }
  function fmtIso(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return `${d.getDate()} ${TH_MONTHS[d.getMonth()]} ${d.getFullYear() + 543} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")} น.`;
  }
  const yearLabel = (y) => (+y === D.fy ? `${y}*` : String(y));
  const shortCol = (label) => label.split(" › ").slice(-2).join(" › ");

  function scopeFor(rep, area) {
    if (rep.st === "group") return "t";  // HDC can't filter category reports below the subdistrict
    return area === "v" && rep.st === "village" ? "v" : "f";
  }
  function seriesOf(rep, scope) {
    return (rep.a && rep.a[scope]) || {};
  }
  function latestOf(byYear, i) {
    const years = Object.keys(byYear).map(Number).sort((a, b) => b - a);
    const pts = years.filter((y) => byYear[y][i] != null).map((y) => ({ year: y, value: byYear[y][i] }));
    return { cur: pts[0] || null, prev: pts[1] || null };
  }

  // ---------------------------------------------------------------- state (URL hash)
  function readState() {
    const p = new URLSearchParams(location.hash.slice(1));
    return {
      q: p.get("q") || "", cat: p.get("cat") || "", a: p.get("a") === "f" ? "f" : "v",
      r: p.get("r") || "", m: p.has("m") ? +p.get("m") : null, y: p.get("y") || "", g: p.get("g") || "", all: p.get("all") === "1",
    };
  }
  let state = readState();
  function hashOf(st) {
    const p = new URLSearchParams();
    if (st.q) p.set("q", st.q);
    if (st.cat) p.set("cat", st.cat);
    if (st.a !== "v") p.set("a", st.a);
    if (st.all) p.set("all", "1");
    if (st.r) p.set("r", st.r);
    if (st.r && st.m != null) p.set("m", st.m);
    if (st.r && st.y) p.set("y", st.y);
    if (st.r && st.g) p.set("g", st.g);
    return "#" + p.toString();
  }
  function go(patch, { replace = false } = {}) {
    const next = { ...state, ...patch };
    const url = hashOf(next);
    if (replace) {
      history.replaceState(null, "", url);
      state = next;
      render({ keepScroll: true });
    } else {
      location.hash = url;  // hashchange -> render
    }
  }
  const link = (patch) => hashOf({ ...state, ...patch });

  // ---------------------------------------------------------------- search
  const byCode = new Map(D ? D.reports.map((r) => [r.c, r]) : []);
  function termsOf(q) {
    return q.toLowerCase().split(/\s+/).filter(Boolean).map((t) => [t, ...(SYN[t] || [])]);
  }
  function search(q, cat) {
    const terms = termsOf(q);
    const out = [];
    for (const r of D.reports) {
      if (cat && r.cat !== cat) continue;
      let score = 0;
      if (terms.length) {
        const title = r.t.toLowerCase(), path = `${r.cat} ${r.sub} ${r.tbl}`.toLowerCase(), note = (r.n || "").toLowerCase();
        let miss = false;
        for (const alts of terms) {
          let best = 0;
          alts.forEach((t, i) => {
            const w = i ? 0.8 : 1;
            best = Math.max(best, title.includes(t) ? 3 * w : path.includes(t) ? 1.5 * w : note.includes(t) ? 0.5 * w : 0);
          });
          if (!best) { miss = true; break; }
          score += best;
        }
        if (miss) continue;
      }
      out.push({ r, score });
    }
    out.sort((a, b) => b.score - a.score || HAS_DATA(b.r.st) - HAS_DATA(a.r.st) || a.r.t.localeCompare(b.r.t, "th"));
    return out.map((o) => o.r);
  }
  function highlight(text, q) {
    const words = termsOf(q).flat().filter((w) => w.trim().length > 0);
    if (!words.length) return [text];
    const re = new RegExp("(" + words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")", "ig");
    return text.split(re).map((part, i) => (i % 2 ? h("mark", null, part) : part));
  }

  // ---------------------------------------------------------------- small pieces
  function badge(st) {
    const [cls, label] = STATUS[st] || STATUS.none;
    return h("span", { class: "badge " + cls }, label);
  }
  function sparkline(byYear, i, width = 96, height = 30) {
    const years = Object.keys(byYear).map(Number).sort((a, b) => a - b);
    const pts = years.map((y) => byYear[y][i]).map((v, k) => [k, v]).filter(([, v]) => v != null);
    if (pts.length < 2) return null;
    const vals = pts.map(([, v]) => v), lo = Math.min(...vals), hi = Math.max(...vals);
    const x = (k) => 4 + (k / Math.max(1, years.length - 1)) * (width - 8);
    const y = (v) => hi === lo ? height / 2 : height - 4 - ((v - lo) / (hi - lo)) * (height - 8);
    const d = pts.map(([k, v], j) => `${j ? "L" : "M"}${x(k).toFixed(1)},${y(v).toFixed(1)}`).join("");
    const [lk, lv] = pts[pts.length - 1];
    return s("svg", { viewBox: `0 0 ${width} ${height}`, width, height, "aria-hidden": "true" },
      s("path", { d, fill: "none", stroke: css("--bench-2"), "stroke-width": 1.5, "stroke-linejoin": "round", "stroke-linecap": "round" }),
      s("circle", { cx: x(lk), cy: y(lv), r: 3.5, fill: css("--series-1"), stroke: css("--surface"), "stroke-width": 1.5 }));
  }
  function showTip(evtOrRect, header, rows) {
    tip.replaceChildren(h("div", { class: "h" }, header),
      ...rows.map(([name, color, value]) => h("div", { class: "r" },
        h("span", null, color ? h("i", { style: `background:${color}` }) : null, name), h("b", null, value))));
    tip.hidden = false;
    const pad = 14, tw = tip.offsetWidth, th = tip.offsetHeight;
    let x, y;
    if (evtOrRect.clientX != null) { x = evtOrRect.clientX + pad; y = evtOrRect.clientY + pad; }
    else { x = evtOrRect.right + pad; y = evtOrRect.top; }
    if (x + tw > innerWidth - 8) x = Math.max(8, x - tw - pad * 2);
    if (y + th > innerHeight - 8) y = Math.max(8, innerHeight - th - 8);
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }
  const hideTip = () => { tip.hidden = true; };

  // ---------------------------------------------------------------- charts
  function niceMax(v) {
    if (v <= 0) return 1;
    const p = 10 ** Math.floor(Math.log10(v)), n = v / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
  }
  function lineChart(box, { years, series, unit }) {
    box.replaceChildren();
    const all = series.flatMap((se) => se.values).filter((v) => v != null);
    if (!all.length) { box.append(h("p", { class: "sub" }, "ไม่มีข้อมูลสำหรับตัวชี้วัดนี้")); return; }
    const W = Math.max(300, box.clientWidth), H = 250, m = { l: 52, r: 16, t: 14, b: 30 };
    const pw = W - m.l - m.r, ph = H - m.t - m.b;
    const lo = Math.min(0, ...all), top = niceMax(Math.max(...all));
    const x = (i) => m.l + (years.length === 1 ? pw / 2 : (i / (years.length - 1)) * pw);
    const y = (v) => m.t + ph - ((v - lo) / (top - lo)) * ph;
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `แนวโน้ม ${unit}` });
    for (let k = 0; k <= 4; k++) {
      const v = lo + ((top - lo) * k) / 4, yy = y(v);
      svg.append(s("line", { x1: m.l, x2: W - m.r, y1: yy, y2: yy, stroke: css(k ? "--line" : "--axis"), "stroke-width": 1 }));
      svg.append(s("text", { x: m.l - 8, y: yy + 4, "text-anchor": "end", class: "num" }, fmt(v)));
    }
    years.forEach((yr, i) => svg.append(s("text", { x: x(i), y: H - 8, "text-anchor": "middle" }, yearLabel(yr))));
    // draw benchmarks first so the area of interest sits on top
    [...series].reverse().forEach((se) => {
      let d = "", pen = false;
      se.values.forEach((v, i) => {
        if (v == null) { pen = false; return; }
        d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
        pen = true;
      });
      svg.append(s("path", { d, fill: "none", stroke: se.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      se.values.forEach((v, i) => {
        if (v != null) svg.append(s("circle", { cx: x(i), cy: y(v), r: 4, fill: se.color, stroke: css("--surface"), "stroke-width": 2 }));
      });
    });
    // direct label: last value of the series the page is about
    const main = series[0], lastI = main.values.map((v, i) => (v != null ? i : -1)).filter((i) => i >= 0).pop();
    if (lastI != null) {
      const lx = x(lastI), anchor = lx > W - 70 ? "end" : "start";
      svg.append(s("text", { x: lx + (anchor === "end" ? -8 : 8), y: y(main.values[lastI]) - 10, "text-anchor": anchor, class: "end-label num" }, fmt(main.values[lastI])));
    }
    const cross = s("line", { y1: m.t, y2: m.t + ph, stroke: css("--axis"), "stroke-width": 1, visibility: "hidden" });
    svg.append(cross);
    const hit = s("rect", { x: m.l - 20, y: m.t, width: pw + 40, height: ph, fill: "transparent" });
    svg.append(hit);
    const showAt = (i, evt) => {
      cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
      const rows = series.map((se) => [se.name, se.color, fmt(se.values[i])]);
      const rect = svg.getBoundingClientRect(), scale = rect.width / W;
      showTip(evt || { right: rect.left + x(i) * scale, top: rect.top + m.t * scale }, `ปีงบ ${yearLabel(years[i])} · ${unit}`, rows);
    };
    const idxAt = (evt) => {
      const rect = svg.getBoundingClientRect(), px = ((evt.clientX - rect.left) / rect.width) * W;
      let best = 0;
      years.forEach((_, i) => { if (Math.abs(x(i) - px) < Math.abs(x(best) - px)) best = i; });
      return best;
    };
    hit.addEventListener("pointermove", (e) => showAt(idxAt(e), e));
    hit.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); hideTip(); });
    let kIdx = years.length - 1;
    const wrap = h("div", { class: "chart", tabindex: 0, "aria-label": "กราฟแนวโน้ม ใช้ลูกศรซ้าย/ขวาเพื่อดูค่าแต่ละปี" }, svg);
    wrap.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      kIdx = Math.max(0, Math.min(years.length - 1, kIdx + (e.key === "ArrowRight" ? 1 : -1)));
      showAt(kIdx);
    });
    wrap.addEventListener("focus", () => showAt(kIdx));
    wrap.addEventListener("blur", () => { cross.setAttribute("visibility", "hidden"); hideTip(); });
    box.append(wrap);
  }

  function villageBars(box, { items, unit, year, solo = false }) {
    box.replaceChildren();
    const vals = items.map((it) => it.value).filter((v) => v != null);
    if (!vals.length) { box.append(h("p", { class: "sub" }, "ไม่มีข้อมูลรายหมู่บ้านในปีนี้")); return; }
    const max = Math.max(...vals) || 1;
    const list = h("div", { class: "bars" });
    for (const it of items) {
      const ratio = it.value == null ? 0 : Math.max(0, it.value) / max;
      const row = h("div", { class: "bar-row" + (it.me ? " me" : "") + (solo ? " solo" : ""), tabindex: 0 },
        h("span", { class: "lbl", title: it.label }, it.label),
        h("span", { class: "bar-track" },
          it.value == null ? null : h("span", { class: "bar", style: `width:calc((100% - 72px) * ${ratio.toFixed(4)})` }),
          h("span", { class: "bar-val num" }, fmt(it.value))));
      const show = (e) => showTip(e.clientX != null ? e : row.getBoundingClientRect(), `ปีงบ ${yearLabel(year)} · ${unit}`, [[it.label, null, fmt(it.value)]]);
      row.addEventListener("pointermove", show);
      row.addEventListener("pointerleave", hideTip);
      row.addEventListener("focus", show);
      row.addEventListener("blur", hideTip);
      list.append(row);
    }
    box.append(list);
  }

  // ---------------------------------------------------------------- views
  let redrawCharts = null;

  function viewMissing() {
    mount(app, h("div", { class: "empty" },
      h("p", null, "ยังไม่มีไฟล์ข้อมูล (data.js)"),
      h("p", null, "รอให้ระบบดึงข้อมูลรอบแรกเสร็จ หรือรัน python3 ~/hdc_opendata/build_site.py")));
  }

  function tile(rep, area) {
    const scope = scopeFor(rep, area), data = seriesOf(rep, scope), i = rep.p;
    const { cur, prev } = latestOf(data, i);
    if (!cur) return null;
    const col = rep.cols[i];
    return h("a", { class: "tile", href: link({ r: rep.c, m: null, y: "" }) },
      h("div", { class: "t", title: rep.t }, rep.t),
      h("div", null,
        h("div", { class: "val" }, fmt(cur.value)),
        h("span", { class: "unit" }, `${shortCol(col.l)} · ปีงบ ${yearLabel(cur.year)}` + (scope !== area ? " · ระดับ รพ.สต." : ""))),
      h("div", { class: "foot" },
        h("span", { class: "delta" }, prev ? `${fmtDelta(cur.value - prev.value)} จากปี ${prev.year}` : ""),
        sparkline(data, i)));
  }

  function viewHome() {
    const area = state.a;
    const c = D.counts || {};
    const withData = D.reports.filter((r) => HAS_DATA(r.st));
    const used = new Set();
    const tiles = [];
    for (const words of KEY) {
      const rep = withData.find((r) => !used.has(r.c) && words.every((w) => r.t.includes(w)) && r.cols[r.p]?.k === "rate"
        && latestOf(seriesOf(r, scopeFor(r, area)), r.p).cur && (area === "f" || r.st === "village"));
      if (rep) { used.add(rep.c); tiles.push(tile(rep, area)); }
      if (tiles.length >= 8) break;
    }
    const cats = [...new Set(D.reports.map((r) => r.cat))];
    mount(app, 
      h("section", { class: "hello" },
        h("h1", null, `ข้อมูลสุขภาพ ${AREA[area]}`),
        h("p", null, area === "v"
          ? "ตัวเลขของผู้ที่อาศัยในหมู่ 11 ต.บ้านเหล่า จากรายงานมาตรฐาน HDC ย้อนหลัง 5 ปีงบประมาณ รายงานที่ HDC ไม่แยกรายหมู่บ้านจะแสดงยอดรวมของ รพ.สต.บ้านแดงแทน"
          : "ตัวเลขรวมทั้งเขตรับผิดชอบของ รพ.สต.บ้านแดง (หมู่ 1, 6, 9, 10, 11, 12, 14 ต.บ้านเหล่า) จากรายงานมาตรฐาน HDC ย้อนหลัง 5 ปีงบประมาณ"),
        h("div", { class: "summary" },
          [["รายงานใน HDC", D.reports.length], ["มีข้อมูลรายหมู่บ้าน", c.village || 0],
           ["มีข้อมูลระดับ รพ.สต.", c.facility || 0],
           c.pending ? ["กำลังดึงข้อมูล", c.pending] : ["ไม่มีข้อมูลของพื้นที่", (c.none || 0) + (c.unavailable || 0)]]
            .map(([k, v]) => h("div", { class: "cell" }, h("div", { class: "v" }, fmt(v)), h("div", { class: "k" }, k))))),
      h("h2", { class: "section" }, "ค้นหาตามหัวข้อ"),
      h("div", { class: "chips" }, TOPICS.map((t) => h("a", { class: "chip", href: link({ q: t, cat: "", r: "" }) }, t))),
      tiles.length ? h("h2", { class: "section" }, "ตัวชี้วัดน่าสนใจ", h("small", null, "ค่าล่าสุด · * = ปีงบปัจจุบัน ข้อมูลยังสะสมอยู่")) : null,
      tiles.length ? h("div", { class: "tiles" }, tiles) : null,
      h("h2", { class: "section" }, "เลือกดูตามหมวด"),
      h("div", { class: "cats" }, cats.map((cat) => {
        const n = D.reports.filter((r) => r.cat === cat && HAS_DATA(r.st)).length;
        const total = D.reports.filter((r) => r.cat === cat).length;
        return h("a", { class: "cat", href: link({ cat, q: "", r: "" }) }, h("b", null, cat), h("span", null, `มีข้อมูล ${n} จาก ${total} รายงาน`));
      })));
  }

  let shown = 60;
  function viewResults() {
    const res = search(state.q, "");
    const inCat = state.cat ? res.filter((r) => r.cat === state.cat) : res;
    const visible = state.all ? inCat : inCat.filter((r) => HAS_DATA(r.st));
    const hidden = inCat.length - visible.length;
    const catCounts = {};
    res.forEach((r) => { if (state.all || HAS_DATA(r.st)) catCounts[r.cat] = (catCounts[r.cat] || 0) + 1; });

    const list = h("div", { class: "list" });
    for (const rep of visible.slice(0, shown)) list.append(resultRow(rep));
    mount(app, 
      h("h1", { style: "font-size:22px;margin-bottom:14px" }, state.q ? `ผลค้นหา “${state.q}”` : state.cat || "รายงานทั้งหมด"),
      h("div", { class: "filters" },
        h("div", { class: "chips" },
          h("a", { class: "chip", href: link({ cat: "" }), "aria-pressed": String(!state.cat) }, "ทุกหมวด"),
          Object.keys(catCounts).map((cat) => h("a", { class: "chip", href: link({ cat }), "aria-pressed": String(state.cat === cat) },
            cat, h("span", { class: "count" }, catCounts[cat])))),
        h("label", { class: "toggle" },
          h("input", { type: "checkbox", checked: state.all, onchange: (e) => go({ all: e.target.checked }, { replace: true }) }),
          "แสดงรายงานที่ไม่มีข้อมูลด้วย")),
      h("p", { class: "count-line" }, `พบ ${fmt(visible.length)} รายงาน` + (hidden ? ` · ซ่อน ${fmt(hidden)} รายงานที่ไม่มีข้อมูลของพื้นที่นี้` : "")),
      visible.length ? list : h("div", { class: "empty" }, "ไม่พบรายงาน ลองคำอื่น เช่น เบาหวาน ผู้สูงอายุ วัคซีน"),
      visible.length > shown ? h("button", { class: "btn more", type: "button", onclick: () => { shown += 60; render({ keepScroll: true }); } },
        `แสดงเพิ่ม (${fmt(visible.length - shown)})`) : null);
  }

  function resultRow(rep) {
    const has = HAS_DATA(rep.st);
    const right = h("div", { class: "right" }, badge(rep.st));
    if (has) {
      const scope = scopeFor(rep, state.a), data = seriesOf(rep, scope);
      const { cur } = latestOf(data, rep.p);
      if (cur) {
        right.append(h("div", { class: "latest num" }, fmt(cur.value),
          h("small", null, `${shortCol(rep.cols[rep.p].l)} · ${yearLabel(cur.year)}`)));
        const sp = sparkline(data, rep.p);
        if (sp) right.append(sp);
      }
    }
    return h("a", { class: "row" + (has ? "" : " dim"), href: link({ r: rep.c, m: null, y: "" }) },
      h("div", null, h("div", { class: "t" }, highlight(rep.t, state.q)), h("div", { class: "path" }, `${rep.cat} › ${rep.sub}`)),
      right);
  }

  function viewDetail() {
    const rep = byCode.get(state.r);
    const back = h("a", { class: "back", href: link({ r: "", m: null, y: "" }) }, "← ", state.q || state.cat ? "กลับไปผลค้นหา" : "หน้าแรก");
    if (!rep) { mount(app, back, h("div", { class: "empty" }, "ไม่พบรายงานนี้")); return; }
    const hdcUrl = D.hdcUrl.replace("{code}", rep.c).replace(rep.sid ? "{sid}" : "?subcatalogId={sid}", rep.sid || "");
    const hdcLink = h("a", { class: "btn", href: hdcUrl, target: "_blank", rel: "noopener",
      title: "บนเว็บ HDC ให้เลือก อำเภอ = บ้านฝาง, ตำบล = บ้านเหล่า แล้วกด ดูรายงาน" }, "เปิดรายงานนี้บนเว็บ HDC ↗");
    const hdcHow = h("p", { class: "fine" },
      "เว็บ HDC ไม่รับตัวกรองผ่านลิงก์ เมื่อเปิดแล้วให้เลือก อำเภอ = บ้านฝาง · ตำบล = บ้านเหล่า แล้วกด “ดูรายงาน” จะเห็นตัวเลขชุดเดียวกัน " +
      (rep.st === "village" ? "(มุมมองรายพื้นที่ แถว 11 = บ้านบึงสว่าง; มุมมองรายหน่วยบริการ แถว 13896 = รพ.สต.บ้านแดง)"
        : rep.st === "group" ? "(ตารางแยกกลุ่มของ ต.บ้านเหล่า)" : "(มุมมองรายหน่วยบริการ แถว 13896 = รพ.สต.บ้านแดง)"));
    const head = h("div", { class: "d-head" },
      h("h1", null, rep.t),
      h("div", { class: "path" }, `${rep.cat} › ${rep.sub}`),
      h("div", { class: "meta" }, badge(rep.st), rep.dc ? h("span", { class: "fine", style: "margin:0" }, `HDC ประมวลผลล่าสุด ${fmtDateCom(rep.dc)}`) : null));
    if (!HAS_DATA(rep.st)) {
      const why = {
        none: "HDC ไม่มีตัวเลขของ รพ.สต.บ้านแดง ในรายงานนี้ (มักเป็นรายงานของโรงพยาบาล หรือบริการที่ รพ.สต. ไม่ได้ให้)",
        pending: "ระบบยังดึงข้อมูลรายงานนี้ไม่ถึง จะมีข้อมูลหลังรอบดึงถัดไป",
        province: "บนเว็บ HDC รายงานนี้ดูได้เฉพาะระดับจังหวัด/เขตสุขภาพ ไม่มีตัวเลขของหมู่บ้านหรือ รพ.สต.",
        unavailable: "MOPH Open Data ไม่เปิดตารางของรายงานนี้ ดูได้เฉพาะบนเว็บ HDC",
        nodef: "HDC ไม่ได้ระบุโครงสร้างรายงานนี้",
      }[rep.st];
      mount(app, back, head, h("div", { class: "note" }, why), h("div", { class: "actions" }, hdcLink));
      return;
    }

    const scope = scopeFor(rep, state.a);
    const data = seriesOf(rep, scope);
    const cols = rep.cols;
    let mi = state.m != null && cols[state.m] ? state.m : rep.p;
    const col = cols[mi];
    const isRate = col.k === "rate";
    const years = [...new Set(["v", "f", "d", "p"].flatMap((k) => Object.keys(seriesOf(rep, k))).map(Number))]
      .filter((y) => y <= D.fy).sort((a, b) => a - b);
    const { cur, prev } = latestOf(data, mi);

    // metric picker
    const pick = h("select", { id: "metric", onchange: (e) => go({ m: +e.target.value }, { replace: true }) });
    const groups = [["rate", "อัตรา / ร้อยละ"], ["count", "จำนวน"]];
    for (const [k, label] of groups) {
      const og = h("optgroup", { label });
      cols.forEach((c, i) => { if (c.k === k) og.append(h("option", { value: i, selected: i === mi }, c.l)); });
      if (og.children.length) pick.append(og);
    }

    // stat tiles
    const benchAt = (k) => (cur ? (seriesOf(rep, k)[cur.year] || [])[mi] : null);
    const stats = [h("div", { class: "stat hero" },
      h("div", { class: "k" }, `${SCOPE_NAME[scope]} · ปีงบ ${cur ? yearLabel(cur.year) : "–"}`),
      h("div", { class: "v" }, cur ? fmt(cur.value) : "–"),
      h("div", { class: "s" }, shortCol(col.l)))];
    stats.push(h("div", { class: "stat" },
      h("div", { class: "k" }, prev ? `เทียบปีงบ ${prev.year}` : "ปีก่อนหน้า"),
      h("div", { class: "v" }, prev ? fmtDelta(cur.value - prev.value) : "–"),
      h("div", { class: "s" }, prev ? `ปี ${prev.year} = ${fmt(prev.value)}` : "ไม่มีข้อมูลปีก่อน")));
    const benchKeys = isRate ? (scope === "v" ? ["f", "d", "p"] : ["d", "p"]) : (scope === "v" ? ["f"] : []);
    for (const k of benchKeys.slice(-2)) {
      stats.push(h("div", { class: "stat" },
        h("div", { class: "k" }, `${SCOPE_NAME[k]} · ปีงบ ${cur ? yearLabel(cur.year) : "–"}`),
        h("div", { class: "v" }, fmt(benchAt(k))),
        h("div", { class: "s" }, isRate ? "ค่าเดียวกันของพื้นที่เปรียบเทียบ" : "จำนวนรวมทั้ง รพ.สต.")));
    }

    // trend chart
    const chartSeries = [{ name: SCOPE_NAME[scope], color: css("--series-1"), values: years.map((y) => (data[y] || [])[mi] ?? null) }];
    if (isRate) {
      (scope === "v" ? ["f", "d", "p"] : ["d", "p"]).forEach((k, j) => {
        const sd = seriesOf(rep, k);
        const values = years.map((y) => (sd[y] || [])[mi] ?? null);
        if (values.some((v) => v != null)) chartSeries.push({ name: SCOPE_NAME[k], color: css(BENCH_VAR[j]), values });
      });
    }
    const trendBox = h("div");
    const trendPanel = h("div", { class: "panel" },
      h("h3", null, "แนวโน้มรายปี"),
      h("p", { class: "sub" }, col.l + (isRate ? "" : " · เทียบได้เฉพาะพื้นที่เดียวกัน (จำนวนไม่เทียบข้ามพื้นที่)")),
      chartSeries.length > 1 ? h("div", { class: "legend" }, chartSeries.map((se) => h("span", null, h("i", { style: `background:${se.color}` }), se.name))) : null,
      trendBox);

    // village comparison
    let villagePanel = null, villageBox = null;
    const vilYears = Object.keys(rep.vil || {}).map(Number).sort((a, b) => b - a);
    const vy = vilYears.includes(+state.y) ? +state.y : vilYears.find((y) => Object.values(rep.vil[y]).some((v) => v[mi] != null)) || vilYears[0];
    if (rep.st === "village" && vilYears.length) {
      villageBox = h("div");
      const ysel = h("select", { onchange: (e) => go({ y: e.target.value }, { replace: true }) },
        vilYears.map((y) => h("option", { value: y, selected: y === vy }, `ปีงบ ${yearLabel(y)}`)));
      villagePanel = h("div", { class: "panel" },
        h("h3", null, "เทียบหมู่บ้านใน รพ.สต.บ้านแดง"),
        h("p", { class: "sub" }, shortCol(col.l)),
        h("div", { class: "field narrow", style: "margin-bottom:12px" }, ysel),
        villageBox);
    }

    // category breakdown (HDC "other" view): บ้านแดง only
    let groupPanel = null, groupBox = null, groupTable = null;
    const grpScopes = rep.grp ? ["t", "d", "p"].filter((k) => rep.grp[k]) : [];
    const gs = grpScopes.includes(state.g) ? state.g : grpScopes[0];
    const grp = gs && rep.grp[gs];
    const grpYears = grp ? Object.keys(grp).map(Number).sort((a, b) => b - a) : [];
    const gy = grpYears.includes(+state.y) ? +state.y : grpYears[0];
    if (grp && gy) {
      groupBox = h("div");
      const gsel = h("select", { onchange: (e) => go({ y: e.target.value }, { replace: true }) },
        grpYears.map((y) => h("option", { value: y, selected: y === gy }, `ปีงบ ${yearLabel(y)}`)));
      groupTable = h("table", null,
        h("thead", null, h("tr", null, h("th", null, "กลุ่ม"), cols.map((c) => h("th", { class: "num" }, shortCol(c.l))))),
        h("tbody", null, grp[gy].map(([label, vals]) => h("tr", null, h("td", null, label),
          vals.map((v) => h("td", { class: "num" + (v == null ? " na" : "") }, fmt(v)))))));
      const asel = h("select", { onchange: (e) => go({ g: e.target.value }, { replace: true }) },
        grpScopes.map((k) => h("option", { value: k, selected: k === gs }, SCOPE_NAME[k])));
      groupPanel = h("div", { class: "panel" },
        h("h3", null, `แยกตามกลุ่ม · ${SCOPE_NAME[gs]}`),
        h("p", { class: "sub" }, `${shortCol(col.l)} · ตารางเดียวกับบนเว็บ HDC`),
        h("div", { class: "controls", style: "margin:0 0 12px" },
          h("div", { class: "field narrow" }, asel), h("div", { class: "field narrow" }, gsel)),
        groupBox,
        h("div", { class: "table-wrap", style: "margin-top:16px" }, groupTable));
    }

    // table
    const lastYear = years.filter((y) => data[y]).pop();
    const benchCols = ["f", "d", "p"].filter((k) => k !== scope && Object.keys(seriesOf(rep, k)).length);
    const table = h("table", null,
      h("thead", null, h("tr", null, h("th", null, "ตัวชี้วัด"),
        years.map((y) => h("th", { class: "num" }, `ปีงบ ${yearLabel(y)}`)),
        lastYear ? benchCols.map((k) => h("th", { class: "bench" }, `${SCOPE_NAME[k]} ${lastYear}`)) : null)),
      h("tbody", null, cols.map((c, i) => h("tr", { class: i === mi ? "sel" : "", onclick: () => go({ m: i }, { replace: true }) },
        h("td", null, c.l, h("span", { class: "kind" }, c.k === "rate" ? "อัตรา" : "จำนวน")),
        years.map((y) => { const v = (data[y] || [])[i]; return h("td", { class: "num" + (v == null ? " na" : "") }, fmt(v)); }),
        lastYear ? benchCols.map((k) => {
          const v = (seriesOf(rep, k)[lastYear] || [])[i];
          const show = c.k === "rate" || k === "f";  // counts only compare within บ้านแดง
          return h("td", { class: "num bench" + (v == null || !show ? " na" : "") }, show ? fmt(v) : "·");
        }) : null))));

    const csvBtn = h("button", { class: "btn", type: "button", onclick: () => downloadCsv(rep, scope, years) }, "ดาวน์โหลดตาราง (CSV)");
    const partial = years.includes(D.fy);
    mount(app, 
      back, head,
      rep.st === "group" ? h("div", { class: "note" },
        "รายงานนี้ HDC แสดงแยกตามกลุ่ม และกรองพื้นที่ได้ละเอียดสุดแค่ระดับตำบล (ไม่มีตัวกรองรายหมู่บ้านหรือ รพ.สต.) จึงแสดงของ ต.บ้านเหล่า ทั้งตำบล (บ้านแดง + บ้านเหล่า) เหมือนบนเว็บ HDC") : null,
      state.a === "v" && scope === "f" ? h("div", { class: "note" },
        "รายงานนี้ HDC ไม่มีมุมมองรายหมู่บ้าน จึงแสดงยอดรวมของ รพ.สต.บ้านแดง (ครอบคลุมบ้านบึงสว่างด้วย)") : null,
      rep.lvl === "prov" ? h("div", { class: "note" }, "บนเว็บ HDC รายงานนี้แสดงเฉพาะระดับจังหวัด/เขต ตัวเลขของ รพ.สต.บ้านแดงคำนวณจากตารางข้อมูลชุดเดียวกัน") : null,
      h("div", { class: "controls" }, h("label", { class: "field", for: "metric" }, "ตัวชี้วัด", pick)),
      h("div", { class: "stats" }, stats),
      villagePanel ? h("div", { class: "grid2" }, trendPanel, villagePanel) : trendPanel,
      groupPanel,
      h("div", { class: "panel" },
        h("h3", null, "ตารางข้อมูลทั้งหมด"),
        h("p", { class: "sub" }, `${SCOPE_NAME[scope]} · คลิกแถวเพื่อเลือกตัวชี้วัด · คอลัมน์สีเทาคือพื้นที่เปรียบเทียบ ปีงบ ${lastYear || "–"}`),
        h("div", { class: "table-wrap" }, table),
        h("div", { class: "actions" }, csvBtn, hdcLink),
        hdcHow,
        h("p", { class: "fine" },
          (partial ? `* ปีงบ ${D.fy} เป็นปีปัจจุบัน ตัวเลขสะสมถึงวันที่ HDC ประมวลผล · ` : "") +
          (rep.hid ? `ซ่อน ${rep.hid} คอลัมน์ที่ไม่มีค่าหรือคำนวณจากข้อมูลเปิดไม่ได้ · ` : "") +
          "ตัวเลขทุกค่ามาจากเว็บ HDC โดยตรง")),
      rep.n ? h("details", { class: "def" }, h("summary", null, "คำอธิบายและนิยามของรายงาน (จาก HDC)"), h("pre", null, rep.n)) : null);

    redrawCharts = () => {
      lineChart(trendBox, { years, series: chartSeries, unit: shortCol(col.l) });
      if (groupBox) {
        villageBars(groupBox, { year: gy, unit: shortCol(col.l), solo: true,
          items: grp[gy].map(([label, vals]) => ({ label, value: vals[mi] })) });
      }
      if (villageBox) {
        const vd = rep.vil[vy] || {};
        villageBars(villageBox, {
          year: vy, unit: shortCol(col.l),
          items: D.villages.map((v) => ({ label: `ม.${v.moo} ${v.name}`, value: (vd[v.code] || [])[mi] ?? null, me: v.code === D.village })),
        });
      }
    };
    redrawCharts();
  }

  function downloadCsv(rep, scope, years) {
    const esc = (v) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
    const data = seriesOf(rep, scope);
    const lines = [["ตัวชี้วัด", "ประเภท", ...years.map((y) => `ปีงบ ${y}`)].map(esc).join(",")];
    rep.cols.forEach((c, i) => {
      lines.push([c.l, c.k === "rate" ? "อัตรา" : "จำนวน", ...years.map((y) => { const v = (data[y] || [])[i]; return v == null ? "" : String(v); })].map(esc).join(","));
    });
    const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const a = h("a", { href: URL.createObjectURL(blob), download: `${rep.tbl || rep.c}_${scope === "v" ? "บึงสว่าง" : "บ้านแดง"}.csv` });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  // ---------------------------------------------------------------- render loop
  let lastView = "";
  function render({ keepScroll = false } = {}) {
    hideTip();
    redrawCharts = null;
    document.querySelectorAll(".seg button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.area === state.a)));
    if (document.activeElement !== qInput) qInput.value = state.q;
    if (!D) { viewMissing(); return; }
    const view = state.r ? "detail:" + state.r : state.q || state.cat ? "results" : "home";
    if (view !== lastView) shown = 60;
    if (state.r) viewDetail();
    else if (state.q || state.cat) viewResults();
    else viewHome();
    document.title = state.r && byCode.get(state.r) ? `${byCode.get(state.r).t} · สุขภาพบ้านบึงสว่าง` : "สุขภาพบ้านบึงสว่าง";
    if (view !== lastView && !keepScroll) window.scrollTo(0, 0);
    lastView = view;
  }

  window.addEventListener("hashchange", () => { state = readState(); render(); });
  let typing;
  qInput.addEventListener("input", () => {
    clearTimeout(typing);
    typing = setTimeout(() => go({ q: qInput.value.trim(), r: "", m: null, y: "" }, { replace: true }), 180);
  });
  qInput.addEventListener("keydown", (e) => { if (e.key === "Escape") { qInput.value = ""; go({ q: "" }, { replace: true }); } });
  document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => go({ a: b.dataset.area }, { replace: true })));
  let resizeT;
  window.addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(() => redrawCharts && redrawCharts(), 120); });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => render({ keepScroll: true }));

  if (D) {
    document.getElementById("freshness").replaceChildren(
      h("div", null, `ดึงข้อมูลล่าสุด ${fmtIso(D.fetched)}`),
      h("div", null, `อัปเดตหน้าเว็บ ${fmtIso(D.built)}`));
  }
  render();
})();

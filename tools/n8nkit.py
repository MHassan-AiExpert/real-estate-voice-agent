"""Tiny builder for n8n workflow JSON + the shared JavaScript used inside Code nodes.

Everything here is generated into plain n8n workflow JSON (import via Workflows -> Import from file).
"""
import json
import uuid

NS = uuid.UUID("6f1b6a52-0a4c-4f0b-9a55-3a1d2f6b9c11")

# credential key -> (n8n credential type, credential name, fixed credential id)
CRED = {
    "ghl": ("httpBearerAuth", "GoHighLevel Private Integration Token", "crdGhl000000001"),
    "vapi": ("httpCustomAuth", "Vapi API", "crdVapi00000001"),
    "twilio": ("httpBasicAuth", "Twilio", "crdTwilio0000001"),
    "webhook": ("httpHeaderAuth", "Vapi Webhook Secret", "crdWebhook000001"),
    "gcal": ("googleCalendarOAuth2Api", "Google Calendar", "crdGcal000000001"),
    "gsheets": ("googleSheetsOAuth2Api", "Google Sheets", "crdGsheets0000001"),
}

# ---------------------------------------------------------------------------------------------
# JavaScript libraries (included at the top of Code nodes that need them)
# ---------------------------------------------------------------------------------------------
JS_UTIL = r"""
const iso = (ms) => new Date(ms).toISOString();
const clip = (s, n) => { s = (s === undefined || s === null) ? '' : String(s); return s.length > n ? s.slice(0, n - 3) + '...' : s; };
function cfList(cfg, obj) {
  const out = [];
  for (const [k, v] of Object.entries(obj)) {
    if (v === undefined || v === null || v === '') continue;
    const id = (cfg.ghl.custom_fields || {})[k];
    if (!id) continue;
    out.push({ id: id, field_value: v });
  }
  return out;
}
function stageId(cfg, key) { return (cfg.ghl.stage_ids || {})[key] || ''; }
function ghlLink(cfg, id) { return 'https://app.gohighlevel.com/v2/location/' + cfg.ghl.location_id + '/contacts/detail/' + id; }
function errMsg(r) {
  if (!r) return '';
  if (r.error) { const e = r.error; return clip(typeof e === 'string' ? e : (e.message || e.description || JSON.stringify(e)), 160); }
  return '';
}
function safeResp(name) {
  try { const n = $(name); if (n.isExecuted === false) return null; return n.item.json; } catch (e) { return null; }
}
function sheetRows(resp) {
  const v = (resp && resp.values) || [];
  if (!v.length) return [];
  const head = v[0].map((h) => String(h).trim().toLowerCase());
  return v.slice(1).map((row, i) => { const o = { _row: i + 2 }; head.forEach((h, j) => { o[h] = row[j] === undefined ? '' : row[j]; }); return o; });
}
function mkId() { return 'cb_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7); }
"""

JS_PHONE = r"""
const E164 = /^\+[1-9]\d{7,14}$/;
function normalizePhone(raw, cc) {
  if (raw === undefined || raw === null) return '';
  const s = String(raw).trim();
  if (!s) return '';
  const d = s.replace(/[^0-9]/g, '');
  if (!d) return '';
  if (s.startsWith('+')) return '+' + d;
  if (d.startsWith('00')) return '+' + d.slice(2);
  const ccd = String(cc || '+1').replace(/[^0-9]/g, '');
  if (d.startsWith('0')) return '+' + ccd + d.slice(1);
  if (ccd === '1' && d.length === 11 && d.startsWith('1')) return '+' + d;
  if (d.startsWith(ccd) && d.length > 10) return '+' + d;
  return '+' + ccd + d;
}
"""

JS_TZ = r"""
function tzOffsetMs(utcMs, tz) {
  const dtf = new Intl.DateTimeFormat('en-US', { timeZone: tz, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const p = {};
  for (const x of dtf.formatToParts(new Date(utcMs))) p[x.type] = x.value;
  const asUtc = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second);
  return asUtc - Math.floor(utcMs / 1000) * 1000;
}
function isoDow(y, m, d) { const w = new Date(Date.UTC(y, m - 1, d)).getUTCDay(); return w === 0 ? 7 : w; }
function localParts(utcMs, tz) {
  const dtf = new Intl.DateTimeFormat('en-US', { timeZone: tz, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  const p = {};
  for (const x of dtf.formatToParts(new Date(utcMs))) p[x.type] = x.value;
  const y = +p.year, m = +p.month, d = +p.day;
  return { y: y, m: m, d: d, h: +p.hour, min: +p.minute, dow: isoDow(y, m, d) };
}
function zonedToUtc(y, m, d, h, min, tz) {
  const guess = Date.UTC(y, m - 1, d, h, min);
  const off = tzOffsetMs(guess, tz);
  let utc = guess - off;
  const off2 = tzOffsetMs(utc, tz);
  if (off2 !== off) utc = guess - off2;
  return utc;
}
function addDays(y, m, d, n) { const t = new Date(Date.UTC(y, m - 1, d + n)); return { y: t.getUTCFullYear(), m: t.getUTCMonth() + 1, d: t.getUTCDate() }; }
function hm(s) { const a = String(s).split(':').map(Number); return { h: a[0], m: a[1] || 0 }; }
function fmtLabel(utcMs, tz) {
  const dt = new Date(utcMs);
  const wd = new Intl.DateTimeFormat('en-US', { timeZone: tz, weekday: 'long' }).format(dt);
  const md = new Intl.DateTimeFormat('en-US', { timeZone: tz, month: 'long', day: 'numeric' }).format(dt);
  const t = new Intl.DateTimeFormat('en-US', { timeZone: tz, hour: 'numeric', minute: '2-digit' }).format(dt);
  return wd + ', ' + md + ' at ' + t;
}
function inWindow(utcMs, win, tz) {
  const p = localParts(utcMs, tz);
  if (!win.days.includes(p.dow)) return false;
  const s = hm(win.start), e = hm(win.end);
  const mins = p.h * 60 + p.min;
  return mins >= s.h * 60 + s.m && mins < e.h * 60 + e.m;
}
function nextWindowStart(utcMs, win, tz) {
  if (inWindow(utcMs, win, tz)) return utcMs;
  const p = localParts(utcMs, tz);
  const s = hm(win.start);
  for (let i = 0; i < 9; i++) {
    const dd = addDays(p.y, p.m, p.d, i);
    if (!win.days.includes(isoDow(dd.y, dd.m, dd.d))) continue;
    const startUtc = zonedToUtc(dd.y, dd.m, dd.d, s.h, s.m, tz);
    if (startUtc >= utcMs) return startUtc;
  }
  return utcMs + 24 * 3600 * 1000;
}
"""

JS_SLOTS = r"""
function generateSlots(o) {
  const cfg = o.cfg, busy = o.busy, fromMs = o.fromMs, toMs = o.toMs, prefDate = o.prefDate;
  const v = cfg.viewing, tz = cfg.agency.timezone;
  const slotMs = v.slot_minutes * 60000, bufMs = v.buffer_minutes * 60000;
  const st = hm(v.start), en = hm(v.end);
  const first = localParts(fromMs, tz);
  const build = (timeOfDay) => {
    const all = [];
    const span = Math.ceil((toMs - fromMs) / 86400000) + 1;
    for (let i = 0; i <= span; i++) {
      const dd = addDays(first.y, first.m, first.d, i);
      if (!v.days.includes(isoDow(dd.y, dd.m, dd.d))) continue;
      for (let mins = st.h * 60 + st.m; mins + v.slot_minutes <= en.h * 60 + en.m; mins += v.step_minutes) {
        const s = zonedToUtc(dd.y, dd.m, dd.d, Math.floor(mins / 60), mins % 60, tz);
        const e = s + slotMs;
        if (s < fromMs || s >= toMs) continue;
        if (busy.some((b) => s - bufMs < b.e && e + bufMs > b.s)) continue;
        const hr = Math.floor(mins / 60);
        if (timeOfDay === 'morning' && hr >= 12) continue;
        if (timeOfDay === 'afternoon' && (hr < 12 || hr >= 17)) continue;
        if (timeOfDay === 'evening' && hr < 17) continue;
        all.push({ startMs: s, endMs: e, dateKey: dd.y + '-' + String(dd.m).padStart(2, '0') + '-' + String(dd.d).padStart(2, '0') });
      }
    }
    return all;
  };
  let tod = o.timeOfDay && ['morning', 'afternoon', 'evening'].includes(o.timeOfDay) ? o.timeOfDay : '';
  let all = build(tod);
  let relaxed = false;
  if (!all.length && tod) { all = build(''); relaxed = true; }
  let picks = [];
  const same = prefDate ? all.filter((x) => x.dateKey === prefDate) : [];
  if (same.length) {
    const idx = Array.from(new Set([0, Math.floor((same.length - 1) / 2), same.length - 1]));
    picks = idx.map((i) => same[i]);
  } else {
    const seen = new Set();
    for (const x of all) { if (!seen.has(x.dateKey)) { seen.add(x.dateKey); picks.push(x); } if (picks.length >= 3) break; }
  }
  return { relaxed: relaxed, slots: picks.map((x) => ({ startISO: iso(x.startMs), endISO: iso(x.endMs), label: fmtLabel(x.startMs, tz) })) };
}
"""

JS_SCORE = r"""
function scoreLead(q, cfg) {
  const S = cfg.scoring;
  const has = (v) => v !== undefined && v !== null && v !== '' && v !== 'unknown';
  const parts = {};
  parts.timeline = (S.timeline || {})[q.timeline] || 0;
  parts.financing = (S.financing || {})[q.financing] || 0;
  parts.specificity = ((Number(q.budgetMin) > 0 || Number(q.budgetMax) > 0) ? S.specificity.budget : 0)
    + (has(q.preferredArea) ? S.specificity.area : 0)
    + ((q.bedrooms !== undefined && q.bedrooms !== null && q.bedrooms !== '' && !isNaN(Number(q.bedrooms))) ? S.specificity.bedrooms : 0);
  const eng = Math.max((S.engagement.viewing_interest || {})[q.viewingInterest] || 0, q.viewingBooked ? S.engagement.viewing_booked : 0);
  parts.engagement = eng + (q.wantsAgent ? S.engagement.wants_agent : 0);
  let score = Math.min(100, parts.timeline + parts.financing + parts.specificity + parts.engagement);
  let tier = score >= S.hot_min ? 'hot' : (score >= S.warm_min ? 'warm' : 'cold');
  if (q.outcome === 'not_interested') { tier = 'cold'; score = Math.min(score, S.warm_min - 1); }
  return { score: score, tier: tier, parts: parts };
}
"""

LIBS = {"util": JS_UTIL, "phone": JS_PHONE, "tz": JS_TZ, "slots": JS_SLOTS, "score": JS_SCORE}


def lib(*names):
    return "\n".join(LIBS[n] for n in names) + "\n"


class WF:
    def __init__(self, name, test=False, api=None):
        self.name = name
        self.test = test
        self.api = api or {}
        self.nodes = []
        self.conns = {}
        self.names = set()
        self.cx, self.cy = 0, 0

    # ---- layout -------------------------------------------------------------------------
    def at(self, x, y):
        self.cx, self.cy = x, y
        return self

    def _pos(self, x, y):
        if x is None:
            x = self.cx
        if y is None:
            y = self.cy
        self.cx, self.cy = x + 250, y
        return [x, y]

    # ---- primitives ---------------------------------------------------------------------
    def add(self, name, typ, ver, params, x=None, y=None, cred=None, **flags):
        assert name not in self.names, "duplicate node name: " + name
        self.names.add(name)
        node = {
            "parameters": params,
            "id": str(uuid.uuid5(NS, self.name + "|" + name)),
            "name": name,
            "type": typ,
            "typeVersion": ver,
            "position": self._pos(x, y),
        }
        if cred and not self.test:
            t, n, i = CRED[cred]
            node["credentials"] = {t: {"id": i, "name": n}}
        node.update(flags)
        self.nodes.append(node)
        return name

    def link(self, a, b, out=0):
        main = self.conns.setdefault(a, {"main": []})["main"]
        while len(main) <= out:
            main.append([])
        main[out].append({"node": b, "type": "main", "index": 0})

    def seq(self, *names):
        for a, b in zip(names, names[1:]):
            self.link(a, b)

    # ---- node helpers -------------------------------------------------------------------
    def webhook(self, name, path, secured=True, method="POST", x=None, y=None):
        p = {"httpMethod": method, "path": path, "responseMode": "responseNode", "options": {}}
        cred = None
        if secured and not self.test:
            p["authentication"] = "headerAuth"
            cred = "webhook"
        # webhookId makes n8n register the production URL as /webhook/<path> (without it: /webhook/<workflowId>/<node>/<path>)
        return self.add(name, "n8n-nodes-base.webhook", 2, p, x, y, cred=cred,
                        webhookId=str(uuid.uuid5(NS, self.name + "|wh|" + path)))

    def respond_json(self, name, expr, x=None, y=None):
        return self.add(name, "n8n-nodes-base.respondToWebhook", 1.1,
                        {"respondWith": "json", "responseBody": "={{ " + expr + " }}", "options": {}}, x, y)

    def respond_text(self, name, text, content_type="text/xml", x=None, y=None):
        return self.add(name, "n8n-nodes-base.respondToWebhook", 1.1,
                        {"respondWith": "text", "responseBody": text,
                         "options": {"responseHeaders": {"entries": [{"name": "Content-Type", "value": content_type}]}}}, x, y)

    def code(self, name, js, each=False, x=None, y=None):
        p = {"jsCode": js}
        if each:
            p["mode"] = "runOnceForEachItem"
        return self.add(name, "n8n-nodes-base.code", 2, p, x, y)

    def config(self, config_obj, x=None, y=None):
        js = ("// CONFIG (generated from config.json by tools/build_workflows.py).\n"
              "// You can edit values here directly in n8n; re-running the build overwrites them.\n"
              "return [{ json: { cfg: " + json.dumps(config_obj, indent=2) + " } }];\n")
        return self.code("Config", js, x=x, y=y)

    def prep(self, name, js, prev, libs=("util",), x=None, y=None):
        """Code node (per item). `ctx` = copy of previous context node's JSON, `resp` = incoming $json,
        `cfg` = ctx.cfg. The snippet mutates ctx; it is returned automatically."""
        src = "$json" if prev is None else "$('" + prev + "').item.json"
        head = "const prev = " + src + ";\nconst resp = $json;\nconst ctx = Object.assign({}, prev);\nconst cfg = ctx.cfg;\n"
        return self.code(name, lib(*libs) + head + js + "\nreturn { json: ctx };\n", each=True, x=x, y=y)

    def if_(self, name, expr, x=None, y=None):
        cond = {"id": str(uuid.uuid5(NS, name)), "leftValue": "={{ !!(" + expr + ") }}", "rightValue": "",
                "operator": {"type": "boolean", "operation": "true", "singleValue": True}}
        p = {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
                            "conditions": [cond], "combinator": "and"}, "options": {}}
        return self.add(name, "n8n-nodes-base.if", 2.2, p, x, y)

    def noop(self, name, x=None, y=None):
        return self.add(name, "n8n-nodes-base.noOp", 1, {}, x, y)

    def sticky(self, text, x, y, w=420, h=160, color=7):
        n = "Note %d" % (len(self.nodes) + 1)
        self.names.add(n)
        self.nodes.append({"parameters": {"content": text, "height": h, "width": w, "color": color},
                           "id": str(uuid.uuid5(NS, self.name + "|" + n)), "name": n,
                           "type": "n8n-nodes-base.stickyNote", "typeVersion": 1, "position": [x, y]})

    def schedule(self, name, minutes, x=None, y=None):
        return self.add(name, "n8n-nodes-base.scheduleTrigger", 1.2,
                        {"rule": {"interval": [{"field": "minutes", "minutesInterval": minutes}]}}, x, y)

    def error_trigger(self, name, x=None, y=None):
        return self.add(name, "n8n-nodes-base.errorTrigger", 1, {}, x, y)

    def stop_error(self, name, msg_expr, x=None, y=None):
        return self.add(name, "n8n-nodes-base.stopAndError", 1,
                        {"errorType": "errorMessage", "errorMessage": "={{ " + msg_expr + " }}"}, x, y)

    def http(self, name, method, url, cred=None, ctx=None, body=None, form=None, query=None, headers=None,
             onerr=None, retries=3, once=False, timeout=20000, batching=None, x=None, y=None):
        """url / body / form values are JS expressions; '§' is replaced by the ctx node reference."""
        ref = "$('" + ctx + "').item.json" if ctx else "$json"

        def sub(s):
            return s.replace("§", ref)

        p = {"method": method, "url": "={{ " + sub(url) + " }}", "options": {"timeout": timeout}}
        if cred and not self.test:
            t = CRED[cred][0]
            if t.startswith("google"):
                p["authentication"] = "predefinedCredentialType"
                p["nodeCredentialType"] = t
            else:
                p["authentication"] = "genericCredentialType"
                p["genericAuthType"] = t
        hdrs = dict(headers or {})
        if cred == "ghl":
            hdrs.setdefault("Version", "2021-07-28")
            hdrs.setdefault("Accept", "application/json")
        if hdrs:
            p["sendHeaders"] = True
            p["headerParameters"] = {"parameters": [{"name": k, "value": v} for k, v in hdrs.items()]}
        if query:
            p["sendQuery"] = True
            p["queryParameters"] = {"parameters": [{"name": k, "value": "={{ " + sub(v) + " }}"} for k, v in query.items()]}
        if body is not None:
            p["sendBody"] = True
            p["specifyBody"] = "json"
            p["jsonBody"] = "={{ JSON.stringify(" + sub(body) + ") }}"
        if form is not None:
            p["sendBody"] = True
            p["contentType"] = "form-urlencoded"
            p["specifyBody"] = "keypair"
            p["bodyParameters"] = {"parameters": [{"name": k, "value": "={{ " + sub(v) + " }}"} for k, v in form.items()]}
        if batching:
            p["options"]["batching"] = {"batch": {"batchSize": batching[0], "batchInterval": batching[1]}}
        flags = {}
        if retries and retries > 1:
            flags.update({"retryOnFail": True, "maxTries": retries, "waitBetweenTries": 1500})
        if onerr:
            flags["onError"] = onerr
        if once:
            flags["executeOnce"] = True
        return self.add(name, "n8n-nodes-base.httpRequest", 4.2, p, x, y, cred=cred, **flags)

    def export(self, settings=None):
        return {
            "name": self.name,
            "nodes": self.nodes,
            "connections": self.conns,
            "active": False,
            "settings": settings or {"executionOrder": "v1"},
            "pinData": {},
        }

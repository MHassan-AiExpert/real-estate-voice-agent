// Local stand-in for GoHighLevel, Vapi, Twilio, Google Calendar and Google Sheets.
// Used only by the end-to-end test: n8n workflows built with --mock talk to this instead of the real APIs.
import http from 'node:http';
import fs from 'node:fs';

const PORT = Number(process.env.MOCK_PORT || 9099);
const CSV = process.env.LISTINGS_CSV;
const HEAD = {
  Callbacks: ['callback_id', 'created_at', 'due_at', 'contact_id', 'phone', 'first_name', 'reason', 'handler', 'attempt', 'status', 'note'],
  DNC: ['phone', 'added_at', 'source', 'note'],
  CallLog: ['timestamp', 'call_id', 'direction', 'phone', 'contact_id', 'duration_s', 'ended_reason', 'outcome', 'tier', 'score', 'summary', 'recording_url']
};

function parseCsv(text) {
  const rows = []; let row = [], cur = '', q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) { if (c === '"') { if (text[i + 1] === '"') { cur += '"'; i++; } else q = false; } else cur += c; }
    else if (c === '"') q = true;
    else if (c === ',') { row.push(cur); cur = ''; }
    else if (c === '\n') { row.push(cur); rows.push(row); row = []; cur = ''; }
    else if (c !== '\r') cur += c;
  }
  if (cur || row.length) { row.push(cur); rows.push(row); }
  return rows.filter((r) => r.length > 1);
}

let S;
function reset() {
  S = { n: 1, contacts: {}, notes: [], opps: [], calls: [], sms: [], events: [], reqs: [], failSms: false, failCalendar: false,
        sheets: { Listings: CSV ? parseCsv(fs.readFileSync(CSV, 'utf8')) : [['listing_id']], Callbacks: [HEAD.Callbacks.slice()], DNC: [HEAD.DNC.slice()], CallLog: [HEAD.CallLog.slice()] } };
}
reset();

const colIdx = (s) => { let n = 0; for (const ch of s) n = n * 26 + (ch.charCodeAt(0) - 64); return n - 1; };
function parseA1(range) {
  const m = range.match(/^(.+?)!([A-Z]+)(\d*)(?::([A-Z]+)(\d*))?$/);
  if (!m) return null;
  return { tab: m[1], c1: colIdx(m[2]), r1: m[3] ? Number(m[3]) : null, c2: m[4] ? colIdx(m[4]) : colIdx(m[2]), r2: m[5] ? Number(m[5]) : (m[3] ? Number(m[3]) : null) };
}
function setCells(range, values) {
  const a = parseA1(range); if (!a || !S.sheets[a.tab]) return false;
  const sheet = S.sheets[a.tab];
  values.forEach((vals, i) => {
    const r = (a.r1 || 1) - 1 + i;
    while (sheet.length <= r) sheet.push([]);
    vals.forEach((v, j) => { sheet[r][a.c1 + j] = v; });
  });
  return true;
}

const json = (res, code, obj) => { res.writeHead(code, { 'content-type': 'application/json' }); res.end(JSON.stringify(obj)); };

function contactView(c) {
  return { id: c.id, firstName: c.firstName, lastName: c.lastName, phone: c.phone, email: c.email, dnd: !!c.dnd, tags: c.tags, customFields: Object.entries(c.cf).map(([id, value]) => ({ id, value })) };
}

const server = http.createServer(async (req, res) => {
  const chunks = []; for await (const c of req) chunks.push(c);
  const raw = Buffer.concat(chunks).toString();
  const url = new URL(req.url, 'http://x');
  const path = url.pathname;
  let body = {};
  const ct = req.headers['content-type'] || '';
  if (raw) { try { body = ct.includes('form') ? Object.fromEntries(new URLSearchParams(raw)) : JSON.parse(raw); } catch (e) { body = { _raw: raw }; } }
  S.reqs.push({ t: Date.now(), method: req.method, path, query: Object.fromEntries(url.searchParams), headers: { auth: req.headers.authorization ? 'present' : '', ct }, body });
  let m;

  // ---- admin
  if (path === '/__state') return json(res, 200, S);
  if (path === '/__reset') { reset(); return json(res, 200, { ok: true }); }
  if (path === '/__flags' && req.method === 'POST') { Object.assign(S, body); return json(res, 200, { ok: true }); }
  if (path === '/__seed' && req.method === 'POST') {
    if (body.sheet) for (const r of body.rows) S.sheets[body.sheet].push(r);
    if (body.event) S.events.push(body.event);
    return json(res, 200, { ok: true });
  }

  // ---- GoHighLevel
  if (path === '/ghl/contacts/upsert' && req.method === 'POST') {
    let c = Object.values(S.contacts).find((x) => x.phone === body.phone);
    const isNew = !c;
    if (!c) { c = { id: 'ct_' + S.n++, tags: [], cf: {}, notes: [] }; S.contacts[c.id] = c; }
    for (const k of ['firstName', 'lastName', 'email', 'phone', 'source', 'dnd']) if (body[k] !== undefined) c[k] = body[k];
    for (const f of (body.customFields || [])) c.cf[f.id] = f.field_value;
    return json(res, 200, { new: isNew, contact: contactView(c) });
  }
  if ((m = path.match(/^\/ghl\/contacts\/([^/]+)$/)) && req.method === 'GET') {
    const c = S.contacts[m[1]]; return c ? json(res, 200, { contact: contactView(c) }) : json(res, 404, { error: 'Not Found' });
  }
  if ((m = path.match(/^\/ghl\/contacts\/([^/]+)\/tags$/))) {
    const c = S.contacts[m[1]]; if (!c) return json(res, 404, { error: 'Not Found' });
    if (req.method === 'POST') for (const t of body.tags || []) { if (!c.tags.includes(t)) c.tags.push(t); }
    if (req.method === 'DELETE') c.tags = c.tags.filter((t) => !(body.tags || []).includes(t));
    return json(res, 200, { tags: c.tags });
  }
  if ((m = path.match(/^\/ghl\/contacts\/([^/]+)\/notes$/)) && req.method === 'POST') {
    const c = S.contacts[m[1]]; if (!c) return json(res, 404, { error: 'Not Found' });
    S.notes.push({ contactId: m[1], body: body.body }); return json(res, 201, { note: { id: 'n_' + S.n++ } });
  }
  if (path === '/ghl/opportunities/upsert' && req.method === 'POST') {
    let o = S.opps.find((x) => x.contactId === body.contactId && x.pipelineId === body.pipelineId);
    const isNew = !o; if (!o) { o = { id: 'op_' + S.n++ }; S.opps.push(o); }
    Object.assign(o, body); return json(res, 200, { opportunity: o, new: isNew });
  }

  // ---- Vapi
  if (path === '/vapi/call' && req.method === 'POST') {
    const id = 'call_' + S.n++; S.calls.push({ id, body }); return json(res, 201, { id, status: 'queued' });
  }

  // ---- Twilio
  if ((m = path.match(/^\/twilio\/2010-04-01\/Accounts\/([^/]+)\/Messages\.json$/)) && req.method === 'POST') {
    if (S.failSms) return json(res, 400, { code: 21610, message: 'blocked' });
    const sid = 'SM' + S.n++; S.sms.push({ sid, to: body.To, from: body.From, body: body.Body }); return json(res, 201, { sid, status: 'queued' });
  }

  // ---- Google Calendar
  if (path === '/gcal/freeBusy' && req.method === 'POST') {
    if (S.failCalendar) return json(res, 500, { error: 'boom' });
    const min = Date.parse(body.timeMin), max = Date.parse(body.timeMax);
    const busy = S.events.filter((e) => Date.parse(e.start.dateTime) < max && Date.parse(e.end.dateTime) > min).map((e) => ({ start: e.start.dateTime, end: e.end.dateTime }));
    const id = body.items[0].id; return json(res, 200, { calendars: { [id]: { busy } } });
  }
  if ((m = path.match(/^\/gcal\/calendars\/([^/]+)\/events$/)) && req.method === 'POST') {
    if (S.failCalendar) return json(res, 500, { error: 'boom' });
    const ev = Object.assign({ id: 'ev_' + S.n++ }, body); S.events.push(ev); return json(res, 200, { id: ev.id, htmlLink: 'https://calendar.example/' + ev.id });
  }

  // ---- Google Sheets
  if ((m = path.match(/^\/sheets\/([^/]+)\/values\/(.+)$/))) {
    const seg = decodeURIComponent(m[2]);
    if (req.method === 'GET') { const a = parseA1(seg); const t = a && S.sheets[a.tab]; return t ? json(res, 200, { range: seg, values: t }) : json(res, 400, { error: 'bad range ' + seg }); }
    if (req.method === 'POST' && seg.endsWith(':append')) { const a = parseA1(seg.replace(/:append$/, '')); const t = a && S.sheets[a.tab]; if (!t) return json(res, 400, { error: 'bad range' }); for (const r of body.values) t.push(r); return json(res, 200, { updates: { updatedRows: body.values.length } }); }
    if (req.method === 'PUT') { return setCells(seg, body.values) ? json(res, 200, { updatedCells: 1 }) : json(res, 400, { error: 'bad range' }); }
  }
  if ((m = path.match(/^\/sheets\/([^/]+)\/values:batchUpdate$/)) && req.method === 'POST') {
    for (const d of body.data || []) setCells(d.range, d.values);
    return json(res, 200, { totalUpdatedCells: (body.data || []).length });
  }
  return json(res, 404, { error: 'mock: no route for ' + req.method + ' ' + path });
});
server.listen(PORT, '127.0.0.1', () => console.log('mock listening on ' + PORT));

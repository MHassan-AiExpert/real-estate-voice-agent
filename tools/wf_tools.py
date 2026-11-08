"""02a / 02b: n8n workflows that act as the Vapi tool server."""
from n8nkit import WF, lib
from wf_common import (tool_head, tool_tail, twilio_sms, ghl_call, sheets_append, sheets_read, sheets_url,
                       optout_effects, alert_branch, config_node)

FAIL = r"""
const fail = (code, msg) => {
  ctx.vapiResult = { toolCallId: ctx.toolCallId, result: JSON.stringify({ ok: false, error: code, message: msg }) };
  ctx.sideEffects = false;
  return { json: ctx };
};
"""

# ============================ check_availability =====================================================
JS_AVAIL_PREP = r"""
const a = ctx.args || {};
const v = cfg.viewing, tz = cfg.agency.timezone;
const now = Date.now();
const minStart = now + v.min_notice_hours * 3600000;
const pd = (typeof a.preferredDate === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(a.preferredDate)) ? a.preferredDate : '';
let startMs = minStart;
if (pd) { const p = pd.split('-').map(Number); startMs = Math.max(minStart, zonedToUtc(p[0], p[1], p[2], 0, 0, tz)); }
const endMs = startMs + (pd ? 4 : v.horizon_days) * 86400000;
ctx.prefDate = pd;
ctx.fbBody = { timeMin: iso(startMs), timeMax: iso(endMs), timeZone: tz, items: [{ id: cfg.google.calendar_id }] };
"""

JS_AVAIL_SLOTS = FAIL + r"""
const tz = cfg.agency.timezone;
if (resp.error) return fail('calendar_unavailable', 'Could not read the calendar right now.');
const cals = resp.calendars || {};
const cal = cals[cfg.google.calendar_id] || Object.values(cals)[0] || {};
if (cal.errors && cal.errors.length) return fail('calendar_unavailable', 'Calendar not accessible.');
const busy = (cal.busy || []).map((b) => ({ s: Date.parse(b.start), e: Date.parse(b.end) }));
const g = generateSlots({ cfg: cfg, busy: busy, fromMs: Date.parse(ctx.fbBody.timeMin), toMs: Date.parse(ctx.fbBody.timeMax), prefDate: ctx.prefDate, timeOfDay: (ctx.args || {}).timeOfDay });
const out = { ok: true, timezone: tz, slots: g.slots.map((s) => ({ startISO: s.startISO, label: s.label })) };
if (!g.slots.length) out.message = 'No openings found in that window. Try another day.';
if (g.relaxed) out.note = 'Nothing in the requested time of day; these are the nearest alternatives.';
ctx.vapiResult = { toolCallId: ctx.toolCallId, result: JSON.stringify(out) };
ctx.sideEffects = false;
"""

# ============================ book_viewing ===========================================================
JS_BOOK_PREP = r"""
const a = ctx.args || {};
const v = cfg.viewing, tz = cfg.agency.timezone;
const startMs = Date.parse(a.startISO);
const now = Date.now();
ctx.invalid = '';
if (!a.startISO || isNaN(startMs)) ctx.invalid = 'invalid_time';
else if (startMs < now + 5 * 60000) ctx.invalid = 'time_in_past';
else if (!inWindow(startMs, { days: v.days, start: v.start, end: v.end }, tz)) ctx.invalid = 'outside_viewing_hours';
ctx.startMs = isNaN(startMs) ? now : startMs;
ctx.endMs = ctx.startMs + v.slot_minutes * 60000;
const buf = v.buffer_minutes * 60000;
ctx.fbBody = { timeMin: iso(ctx.startMs - buf), timeMax: iso(ctx.endMs + buf), timeZone: tz, items: [{ id: cfg.google.calendar_id }] };
"""

JS_BOOK_EVENT = r"""
if (!ctx.invalid) {
  if (resp.error) ctx.invalid = 'calendar_unavailable';
  else {
    const cals = resp.calendars || {};
    const cal = cals[cfg.google.calendar_id] || Object.values(cals)[0] || {};
    if (cal.errors && cal.errors.length) ctx.invalid = 'calendar_unavailable';
    else if ((cal.busy || []).length) ctx.invalid = 'slot_taken';
  }
}
ctx.slotFree = !ctx.invalid;
const a = ctx.args || {};
const first = String(a.firstName || ctx.firstNameVar || '').trim();
const last = String(a.lastName || '').trim();
ctx.leadFirst = first; ctx.leadLast = last;
ctx.leadName = (first + ' ' + last).trim() || 'Lead';
ctx.email = String(a.email || '').trim();
ctx.listing = clip(a.listing || '', 200);
ctx.label = fmtLabel(ctx.startMs, cfg.agency.timezone);
const desc = ['Booked by the AI voice agent (' + ctx.direction + ' call).', 'Lead: ' + ctx.leadName, 'Phone: ' + ctx.phone,
  ctx.email ? 'Email: ' + ctx.email : '', ctx.listing ? 'Property: ' + ctx.listing : '', a.notes ? 'Notes: ' + clip(a.notes, 300) : ''].filter(Boolean).join('\n');
ctx.eventBody = {
  summary: 'Property viewing: ' + ctx.leadName, description: desc, location: ctx.listing,
  start: { dateTime: iso(ctx.startMs), timeZone: cfg.agency.timezone },
  end: { dateTime: iso(ctx.endMs), timeZone: cfg.agency.timezone },
  reminders: { useDefault: false, overrides: [{ method: 'popup', minutes: 60 }] },
  extendedProperties: { private: { leadPhone: ctx.phone, source: 'ai-voice-agent', callId: ctx.callId || '' } }
};
if (ctx.email) ctx.eventBody.attendees = [{ email: ctx.email }];
"""

JS_BOOK_SMS = r"""
ctx.eventOk = !resp.error && !!resp.id;
ctx.eventId = resp.id || '';
ctx.eventLink = resp.htmlLink || '';
ctx.eventError = errMsg(resp);
const name = ctx.leadFirst || 'there';
ctx.smsTo = ctx.phone;
ctx.smsBody = clip('Hi ' + name + ', your property viewing with ' + cfg.agency.name + ' is confirmed for ' + ctx.label
  + (ctx.listing ? ' (' + ctx.listing + ')' : '') + '. ' + cfg.viewing.meeting_note + ' To change it, call ' + cfg.agency.phone + '. Reply STOP to opt out.', 480);
"""

JS_BOOK_RESULT = r"""
ctx.smsSent = !resp.error && !!resp.sid;
ctx.degraded = [];
if (!ctx.smsSent) ctx.degraded.push('Viewing confirmation SMS NOT sent to ' + ctx.phone + ': ' + (errMsg(resp) || 'no message sid returned'));
ctx.vapiResult = { toolCallId: ctx.toolCallId, result: JSON.stringify({ ok: true, booked: true, label: ctx.label, confirmationTextSent: ctx.smsSent }) };
ctx.sideEffects = true;
"""

JS_BOOK_SLOT_FAIL = r"""
const msgs = {
  slot_taken: 'That time was just taken. Call check_availability again and offer another option.',
  invalid_time: 'The start time was not valid. Use the startISO exactly as returned by check_availability.',
  time_in_past: 'That time has already passed. Call check_availability and offer a later option.',
  outside_viewing_hours: 'That time is outside viewing hours. Call check_availability and offer one of the returned options.',
  calendar_unavailable: 'The calendar is not reachable right now. Apologise and offer an agent callback.'
};
ctx.vapiResult = { toolCallId: ctx.toolCallId, result: JSON.stringify({ ok: false, error: ctx.invalid, message: msgs[ctx.invalid] || 'Could not book that time.' }) };
ctx.sideEffects = false;
ctx.degraded = ctx.invalid === 'calendar_unavailable' ? ['book_viewing: Google Calendar was unreachable for ' + ctx.phone] : [];
"""

JS_BOOK_FAIL = r"""
ctx.vapiResult = { toolCallId: ctx.toolCallId, result: JSON.stringify({ ok: false, error: 'calendar_error', message: 'The booking could not be saved. Apologise and say an agent will confirm by text shortly.' }) };
ctx.sideEffects = false;
ctx.degraded = ['book_viewing: calendar event creation FAILED for ' + ctx.phone + ' at ' + ctx.label + ': ' + ctx.eventError];
"""

JS_BOOK_CRM = r"""
const body = { locationId: cfg.ghl.location_id, phone: ctx.phone, source: ctx.direction === 'inbound' ? 'AI inbound call' : 'AI outbound call' };
if (ctx.leadFirst) body.firstName = ctx.leadFirst;
if (ctx.leadLast) body.lastName = ctx.leadLast;
if (ctx.email) body.email = ctx.email;
body.customFields = cfList(cfg, { ai_viewing_datetime: ctx.label, ai_viewing_event_id: ctx.eventId, ai_viewing_call_id: ctx.callId });
ctx.contactBody = body;
"""

JS_BOOK_CRM2 = r"""
ctx.contactId = (resp && resp.contact && resp.contact.id) || ctx.contactId || '';
ctx.oppBody = { pipelineId: cfg.ghl.pipeline_id, locationId: cfg.ghl.location_id, contactId: ctx.contactId, name: ctx.leadName + ' - viewing', status: 'open', pipelineStageId: stageId(cfg, 'viewing_booked') };
ctx.tagBody = { tags: ['viewing-booked', 'ai-booked'] };
ctx.noteBody = { body: 'AI booked a property viewing for ' + ctx.label + (ctx.listing ? ' (' + ctx.listing + ')' : '') + '. SMS confirmation: ' + (ctx.smsSent ? 'sent' : 'FAILED') + '. Calendar event: ' + (ctx.eventLink || ctx.eventId) };
"""

JS_BOOK_FINAL = r"""
const P = '[Booking] ';
const errs = [];
for (const n of ['GHL Upsert Contact', 'GHL Upsert Opportunity', 'GHL Add Tags', 'GHL Add Note']) { const m = errMsg(safeResp(P + n)); if (m) errs.push(n + ': ' + m); }
ctx.degraded = errs.length ? ['Viewing was booked in Google Calendar for ' + ctx.phone + ' but CRM sync had problems -> ' + errs.join(' | ')] : [];
"""

# ============================ search_listings ========================================================
JS_LISTINGS = FAIL + r"""
const a = ctx.args || {};
if (resp.error) return fail('listings_unavailable', 'Could not read the listings right now.');
const rows = sheetRows(resp);
const lc = (s) => String(s === undefined || s === null ? '' : s).toLowerCase();
const num = (s) => { const n = parseFloat(String(s).replace(/[^0-9.]/g, '')); return isNaN(n) ? null : n; };
const wantType = a.intent === 'buy' ? 'sale' : (a.intent === 'rent' ? 'rent' : '');
const area = lc(a.area).trim();
const areaTokens = area.split(/[^a-z0-9]+/).filter((t) => t.length > 2);
const stop = new Set(['the', 'and', 'for', 'with', 'near', 'any', 'home', 'place', 'properties', 'property', 'listing', 'listings', 'have', 'you']);
const kw = lc([a.query, a.listingRef].filter(Boolean).join(' ')).split(/[^a-z0-9-]+/).filter((t) => t.length > 2 && !stop.has(t));
const ref = lc(a.listingRef).trim();
let list = rows.filter((r) => lc(r.status) === 'active');
if (ref) {
  let hit = list.filter((r) => lc(r.listing_id) === ref);
  if (!hit.length) hit = list.filter((r) => lc([r.address, r.title].join(' ')).includes(ref));
  list = hit;  // an explicit listing reference that matches nothing must return nothing, never "similar" listings
}
if (wantType) list = list.filter((r) => lc(r.type) === wantType);
if (a.minBedrooms !== undefined && a.minBedrooms !== null && a.minBedrooms !== '') list = list.filter((r) => (num(r.bedrooms) || 0) >= Number(a.minBedrooms));
if (a.maxPrice) list = list.filter((r) => num(r.price) !== null && num(r.price) <= Number(a.maxPrice));
if (a.minPrice) list = list.filter((r) => num(r.price) !== null && num(r.price) >= Number(a.minPrice));
if (a.propertyType) list = list.filter((r) => lc(r.property_type).includes(lc(a.propertyType).trim()));
if (area) {
  list = list.filter((r) => {
    const hay = lc([r.neighborhood, r.city, r.address, r.title].join(' '));
    return hay.includes(area) || (areaTokens.length > 0 && areaTokens.every((t) => hay.includes(t)));
  });
}
const hayAll = (r) => lc(Object.values(r).join(' '));
list.forEach((r) => { r._score = kw.filter((t) => hayAll(r).includes(t)).length; });
if (kw.length && !ref) { const matched = list.filter((r) => r._score > 0); if (matched.length) list = matched; }
list.sort((x, y) => (y._score - x._score) || ((num(x.price) || 0) - (num(y.price) || 0)));
const brief = (r) => ({ id: r.listing_id, title: r.title, address: r.address, area: r.neighborhood, city: r.city,
  type: lc(r.type) === 'sale' ? 'for sale' : 'for rent', price: num(r.price), currency: r.currency || '',
  bedrooms: num(r.bedrooms), bathrooms: num(r.bathrooms), sqft: num(r.sqft), propertyType: r.property_type,
  petsAllowed: r.pets_allowed, parking: r.parking, availableFrom: r.available_from, highlights: clip(r.highlights, 200) });
const out = { ok: true, totalMatches: list.length, matches: list.slice(0, 3).map(brief) };
if (!list.length) out.message = 'No active listings match those filters.';
ctx.vapiResult = { toolCallId: ctx.toolCallId, result: JSON.stringify(out) };
ctx.sideEffects = false;
"""

# ============================ save_qualification =====================================================
JS_QUAL_SCORE = r"""
const a = ctx.args || {};
const num = (v) => { if (v === undefined || v === null || v === '') return undefined; const n = Number(String(v).replace(/[^0-9.\-]/g, '')); return isNaN(n) ? undefined : n; };
const slug = (v) => String(v === undefined || v === null || v === '' ? 'unknown' : v).toLowerCase().trim().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'unknown';
const yn = (v) => { if (v === true || v === 'true') return 'yes'; if (v === false || v === 'false') return 'no'; return slug(v); };
const q = {
  intent: ['buy', 'rent'].includes(a.intent) ? a.intent : 'unknown',
  budgetMin: num(a.budgetMin), budgetMax: num(a.budgetMax),
  preferredArea: String(a.preferredArea || '').trim(),
  bedrooms: num(a.bedrooms),
  timeline: slug(a.timeline), financing: slug(a.financing),
  viewingInterest: yn(a.viewingInterest),
  wantsAgent: a.wantsAgent === true || a.wantsAgent === 'true',
  outcome: String(a.outcome || ''), notes: clip(a.notes || '', 400), viewingBooked: false
};
const sc = scoreLead(q, cfg);
ctx.q = q; ctx.score = sc.score; ctx.tier = sc.tier; ctx.scoreParts = sc.parts;
const ag = cfg.agents;
const transferOk = (ag.transfer_numbers || []).length > 0 && inWindow(Date.now(), ag.transfer_hours, cfg.agency.timezone);
ctx.transferAvailable = transferOk && (sc.tier === 'hot' || q.wantsAgent) && q.outcome !== 'not_interested';
ctx.leadFirst = String(a.firstName || ctx.firstNameVar || '').trim();
ctx.leadLast = String(a.lastName || '').trim();
ctx.email = String(a.email || '').trim();
ctx.leadName = (ctx.leadFirst + ' ' + ctx.leadLast).trim() || 'Lead';
"""

JS_QUAL_RESULT = r"""
let hint;
if (ctx.q.outcome === 'not_interested') hint = 'The lead is not interested. Thank them politely and end the call.';
else if (ctx.transferAvailable) hint = 'Strong prospect. Offer a live transfer now; if they decline, offer a viewing.';
else if (ctx.tier === 'hot' || ctx.q.wantsAgent) hint = 'Strong prospect but no agent can take a live transfer right now. Offer a viewing or an agent callback.';
else hint = 'Offer a viewing if they are interested; otherwise offer a later follow-up.';
ctx.vapiResult = { toolCallId: ctx.toolCallId, result: JSON.stringify({ ok: true, tier: ctx.tier, transferAvailable: ctx.transferAvailable, nextStep: hint }) };
ctx.sideEffects = true;
ctx.degraded = [];
"""

JS_QUAL_CRM = r"""
const q = ctx.q;
const body = { locationId: cfg.ghl.location_id, phone: ctx.phone, source: ctx.direction === 'inbound' ? 'AI inbound call' : 'AI outbound call' };
if (ctx.leadFirst) body.firstName = ctx.leadFirst;
if (ctx.leadLast) body.lastName = ctx.leadLast;
if (ctx.email) body.email = ctx.email;
body.customFields = cfList(cfg, {
  ai_intent: q.intent === 'unknown' ? '' : q.intent, ai_budget_min: q.budgetMin, ai_budget_max: q.budgetMax,
  ai_preferred_area: q.preferredArea, ai_bedrooms: q.bedrooms, ai_timeline: q.timeline, ai_financing: q.financing,
  ai_lead_score: ctx.score, ai_lead_tier: ctx.tier, ai_notes: q.notes, ai_last_call_id: ctx.callId
});
ctx.contactBody = body;
"""

JS_QUAL_CRM2 = r"""
const q = ctx.q;
ctx.contactId = (resp && resp.contact && resp.contact.id) || ctx.contactId || '';
const stageKey = q.outcome === 'not_interested' ? 'nurture' : (ctx.tier === 'hot' ? 'qualified_hot' : (ctx.tier === 'warm' ? 'qualified_warm' : 'nurture'));
ctx.oppBody = { pipelineId: cfg.ghl.pipeline_id, locationId: cfg.ghl.location_id, contactId: ctx.contactId, name: ctx.leadName + (q.intent !== 'unknown' ? ' - ' + q.intent : ''), status: 'open', pipelineStageId: stageId(cfg, stageKey) };
const tags = ['ai-qualified', 'tier-' + ctx.tier];
if (q.intent !== 'unknown') tags.push('intent-' + q.intent);
if (q.outcome === 'not_interested') tags.push('not-interested');
ctx.addTags = { tags: tags };
ctx.removeTags = { tags: ['hot', 'warm', 'cold'].filter((t) => t !== ctx.tier).map((t) => 'tier-' + t) };
ctx.alertNumbers = cfg.agents.alert_sms_numbers || [];
ctx.alertNeeded = (ctx.tier === 'hot' || q.wantsAgent) && q.outcome !== 'not_interested';
const bud = q.budgetMax || q.budgetMin;
ctx.alertText = clip((ctx.tier === 'hot' ? 'HOT LEAD' : 'LEAD WANTS AN AGENT') + ' on a live AI call: ' + ctx.leadName + ' ' + ctx.phone
  + '. ' + (q.intent !== 'unknown' ? q.intent : 'intent n/a') + (bud ? ', budget ' + bud : '') + (q.preferredArea ? ', ' + q.preferredArea : '')
  + (q.bedrooms !== undefined ? ', ' + q.bedrooms + ' bed' : '') + ', timeline ' + q.timeline + ', financing ' + q.financing + '.'
  + (ctx.transferAvailable ? ' A live transfer may come through - be ready to answer.' : '') + ' ' + ghlLink(cfg, ctx.contactId), 450);
"""

JS_QUAL_FINAL = r"""
const P = '[Qualify] ';
const errs = [];
for (const n of ['GHL Upsert Contact', 'GHL Upsert Opportunity', 'GHL Add Tags', 'GHL Remove Tags']) { const m = errMsg(safeResp(P + n)); if (m) errs.push(n + ': ' + m); }
ctx.degraded = errs.length ? ['save_qualification CRM sync problem for ' + ctx.phone + ' -> ' + errs.join(' | ')] : [];
"""

# ============================ schedule_callback ======================================================
JS_CB_PREP = r"""
const a = ctx.args || {};
const tz = cfg.agency.timezone;
const reasons = ['busy_now', 'lead_requested', 'wants_agent_callback', 'language', 'other'];
const reason = reasons.includes(a.reason) ? a.reason : 'other';
const handler = (reason === 'wants_agent_callback' || reason === 'language') ? 'human' : 'ai';
const now = Date.now();
let due = NaN;
const m = String(a.callbackLocalTime || '').match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/);
if (m) due = zonedToUtc(+m[1], +m[2], +m[3], +m[4], +m[5], tz);
else if (a.callbackTimeISO) due = Date.parse(a.callbackTimeISO);
if (isNaN(due) || due < now + 5 * 60000) due = now + 2 * 3600000;
if (handler === 'ai') due = nextWindowStart(due, cfg.calling.window, tz);
ctx.cbDueMs = due; ctx.cbLabel = fmtLabel(due, tz); ctx.cbReason = reason; ctx.cbHandler = handler;
ctx.leadFirst = String(a.firstName || ctx.firstNameVar || '').trim();
const attempt = (parseInt(ctx.attemptVar, 10) || 0) + 1;
ctx.cbNotes = clip(a.notes || '', 200);
ctx.cbRow = { values: [[mkId(), iso(now), iso(due), ctx.contactId || '', ctx.phone, ctx.leadFirst, reason, handler, attempt, 'pending', ctx.cbNotes]] };
"""

JS_CB_RESULT = r"""
ctx.vapiResult = { toolCallId: ctx.toolCallId, result: JSON.stringify({ ok: true, scheduledFor: ctx.cbLabel, handledBy: ctx.cbHandler === 'human' ? 'a human agent' : 'our assistant' }) };
ctx.sideEffects = true;
ctx.degraded = [];
"""

JS_CB_CRM = r"""
const body = { locationId: cfg.ghl.location_id, phone: ctx.phone };
if (ctx.leadFirst) body.firstName = ctx.leadFirst;
body.customFields = cfList(cfg, { ai_next_callback_at: ctx.cbLabel });
ctx.contactBody = body;
"""

JS_CB_CRM2 = r"""
ctx.contactId = (resp && resp.contact && resp.contact.id) || ctx.contactId || '';
ctx.tagBody = { tags: ctx.cbHandler === 'human' ? ['callback-scheduled', 'agent-callback'] : ['callback-scheduled'] };
ctx.noteBody = { body: 'Callback scheduled (' + ctx.cbHandler + ') for ' + ctx.cbLabel + '. Reason: ' + ctx.cbReason + (ctx.cbNotes ? '. Notes: ' + ctx.cbNotes : '') };
ctx.alertNumbers = cfg.agents.alert_sms_numbers || [];
ctx.alertNeeded = ctx.cbHandler === 'human';
ctx.alertText = clip('CALLBACK REQUESTED (' + ctx.cbReason + '): ' + (ctx.leadFirst || 'Lead') + ' ' + ctx.phone + ' wants an agent call around ' + ctx.cbLabel + '.' + (ctx.cbNotes ? ' Notes: ' + ctx.cbNotes : '') + ' ' + ghlLink(cfg, ctx.contactId), 450);
"""

JS_CB_FINAL = r"""
const P = '[Callback] ';
const errs = [];
for (const n of ['GHL Upsert Contact', 'GHL Add Tags', 'GHL Add Note']) { const m = errMsg(safeResp(P + n)); if (m) errs.push(n + ': ' + m); }
ctx.degraded = errs.length ? ['schedule_callback CRM sync problem for ' + ctx.phone + ' (callback row WAS saved) -> ' + errs.join(' | ')] : [];
"""

# ============================ opt_out ================================================================
JS_OPT_PREP = r"""
const a = ctx.args || {};
const reasons = ['stop_calling', 'wrong_number', 'not_interested_remove', 'other'];
ctx.optReason = reasons.includes(a.reason) ? a.reason : 'stop_calling';
ctx.optSource = ctx.direction + ' call';
ctx.dncRow = { values: [[ctx.phone, iso(Date.now()), 'voice:' + ctx.direction, ctx.optReason + (ctx.callId ? ' call=' + ctx.callId : '')]] };
"""

JS_OPT_RESULT = r"""
ctx.vapiResult = { toolCallId: ctx.toolCallId, result: JSON.stringify({ ok: true, optedOut: true, message: 'Number removed. End the call politely now.' }) };
ctx.sideEffects = true;
ctx.degraded = [];
"""


def gate_ok(ctx):
    return ctx


def build_tools_a(cfg, A, test):
    wf = WF("02a Vapi Tools - Calendar and Listings", test=test)
    Y = 0
    wf.sticky("## Vapi tool server A\nTools: **check_availability**, **book_viewing**, **search_listings**.\nVapi calls each tool's own webhook, the workflow replies in the `{results:[{toolCallId,result}]}` format "
              "and only afterwards syncs the CRM, so a slow CRM never delays the call.", -40, -260, 620, 150)

    # ---- check_availability
    p = "[Availability] "
    parse = tool_head(wf, p, "check-availability", Y, cfg)
    prep = wf.prep(p + "Prep FreeBusy", JS_AVAIL_PREP, prev=parse, libs=("util", "tz"), y=Y)
    fb = wf.http(p + "Calendar FreeBusy", "POST", "'%s/freeBusy'" % A["gcal"], cred="gcal", ctx=prep, body="§.fbBody",
                 onerr="continueRegularOutput", retries=2, y=Y)
    slots = wf.prep(p + "Compute Slots", JS_AVAIL_SLOTS, prev=prep, libs=("util", "tz", "slots"), y=Y)
    wf.seq(parse, prep, fb, slots)
    tool_tail(wf, p, [slots], Y)

    # ---- book_viewing
    Y = 520
    p = "[Booking] "
    parse = tool_head(wf, p, "book-viewing", Y, cfg)
    pb = wf.prep(p + "Prep Booking", JS_BOOK_PREP, prev=parse, libs=("util", "tz"), y=Y)
    fb = wf.http(p + "Calendar Recheck", "POST", "'%s/freeBusy'" % A["gcal"], cred="gcal", ctx=pb, body="§.fbBody",
                 onerr="continueRegularOutput", retries=2, y=Y)
    pe = wf.prep(p + "Prep Event", JS_BOOK_EVENT, prev=pb, libs=("util", "tz"), y=Y)
    if1 = wf.if_(p + "Slot Free?", "$json.slotFree", y=Y)
    ev = wf.http(p + "Create Event", "POST",
                 "'%s/calendars/' + encodeURIComponent(§.cfg.google.calendar_id) + '/events?sendUpdates=' + (§.eventBody.attendees ? 'all' : 'none')" % A["gcal"],
                 cred="gcal", ctx=pe, body="§.eventBody", onerr="continueRegularOutput", retries=2, x=1500, y=Y)
    ps = wf.prep(p + "Prep SMS", JS_BOOK_SMS, prev=pe, libs=("util",), y=Y)
    if2 = wf.if_(p + "Event Created?", "$json.eventOk", y=Y)
    sms = twilio_sms(wf, p + "Twilio SMS Confirmation", A, cfg, ctx=ps)
    r_ok = wf.prep(p + "Booking Result", JS_BOOK_RESULT, prev=ps, libs=("util",), y=Y)
    r_slot = wf.prep(p + "Slot Unavailable Result", JS_BOOK_SLOT_FAIL, prev=pe, libs=("util",), x=1500, y=Y + 220)
    r_fail = wf.prep(p + "Booking Failed Result", JS_BOOK_FAIL, prev=ps, libs=("util",), x=2250, y=Y + 220)
    wf.seq(parse, pb, fb, pe, if1)
    wf.link(if1, ev, 0)
    wf.link(if1, r_slot, 1)
    wf.seq(ev, ps, if2)
    wf.link(if2, sms, 0)
    wf.link(if2, r_fail, 1)
    wf.link(sms, r_ok)
    fo = tool_tail(wf, p, [r_ok, r_slot, r_fail], Y)
    c1 = wf.prep(p + "Prep CRM", JS_BOOK_CRM, prev=fo, libs=("util",), y=Y + 360)
    up = ghl_call(wf, p + "GHL Upsert Contact", A, "POST", "'/contacts/upsert'", c1, body="§.contactBody", y=Y + 360)
    c2 = wf.prep(p + "Prep CRM 2", JS_BOOK_CRM2, prev=c1, libs=("util",), y=Y + 360)
    op = ghl_call(wf, p + "GHL Upsert Opportunity", A, "POST", "'/opportunities/upsert'", c2, body="§.oppBody", y=Y + 360)
    tg = ghl_call(wf, p + "GHL Add Tags", A, "POST", "'/contacts/' + §.contactId + '/tags'", c2, body="§.tagBody", y=Y + 360)
    nt = ghl_call(wf, p + "GHL Add Note", A, "POST", "'/contacts/' + §.contactId + '/notes'", c2, body="§.noteBody", y=Y + 360)
    fin = wf.prep(p + "Final Check", JS_BOOK_FINAL, prev=c2, libs=("util",), y=Y + 360)
    dg = wf.if_(p + "CRM Problem?", "($json.degraded || []).length > 0", y=Y + 360)
    st = wf.stop_error(p + "Raise CRM Alert", "$json.degraded.join(' | ')", y=Y + 360)
    wf.seq(fo, c1, up, c2, op, tg, nt, fin, dg)
    wf.link(dg, st, 0)

    # ---- search_listings
    Y = 1260
    p = "[Listings] "
    parse = tool_head(wf, p, "search-listings", Y, cfg)
    rd = sheets_read(wf, p + "Sheets Read Listings", A, "listings", "!A1:V500", parse)
    flt = wf.prep(p + "Filter Listings", JS_LISTINGS, prev=parse, libs=("util",), y=Y)
    wf.seq(parse, rd, flt)
    tool_tail(wf, p, [flt], Y)
    return wf


def build_tools_b(cfg, A, test):
    wf = WF("02b Vapi Tools - Leads and Compliance", test=test)
    wf.sticky("## Vapi tool server B\nTools: **save_qualification** (scores the lead, alerts agents), **schedule_callback**, **opt_out**.\n"
              "Opt-out writes the suppression sheet FIRST (critical path), then the CRM.", -40, -260, 620, 150)

    # ---- save_qualification
    Y = 0
    p = "[Qualify] "
    parse = tool_head(wf, p, "save-qualification", Y, cfg)
    ps = wf.prep(p + "Prep Score", JS_QUAL_SCORE, prev=parse, libs=("util", "tz", "score"), y=Y)
    res = wf.prep(p + "Qualification Result", JS_QUAL_RESULT, prev=ps, libs=("util",), y=Y)
    wf.seq(parse, ps, res)
    fo = tool_tail(wf, p, [res], Y)
    c1 = wf.prep(p + "Prep CRM", JS_QUAL_CRM, prev=fo, libs=("util",), y=Y + 360)
    up = ghl_call(wf, p + "GHL Upsert Contact", A, "POST", "'/contacts/upsert'", c1, body="§.contactBody", y=Y + 360)
    c2 = wf.prep(p + "Prep CRM 2", JS_QUAL_CRM2, prev=c1, libs=("util",), y=Y + 360)
    op = ghl_call(wf, p + "GHL Upsert Opportunity", A, "POST", "'/opportunities/upsert'", c2, body="§.oppBody", y=Y + 360)
    ad = ghl_call(wf, p + "GHL Add Tags", A, "POST", "'/contacts/' + §.contactId + '/tags'", c2, body="§.addTags", y=Y + 360)
    rm = ghl_call(wf, p + "GHL Remove Tags", A, "DELETE", "'/contacts/' + §.contactId + '/tags'", c2, body="§.removeTags", y=Y + 360)
    fin = wf.prep(p + "Final Check", JS_QUAL_FINAL, prev=c2, libs=("util",), y=Y + 360)
    dg = wf.if_(p + "CRM Problem?", "($json.degraded || []).length > 0", y=Y + 360)
    st = wf.stop_error(p + "Raise CRM Alert", "$json.degraded.join(' | ')", y=Y + 360)
    alert = alert_branch(wf, p, fin, A, cfg, Y + 560, "$json.alertNeeded")
    wf.seq(fo, c1, up, c2, op, ad, rm, fin, dg)
    wf.link(dg, st, 0)
    wf.link(fin, alert)

    # ---- schedule_callback
    Y = 1000
    p = "[Callback] "
    parse = tool_head(wf, p, "schedule-callback", Y, cfg)
    pc = wf.prep(p + "Prep Callback", JS_CB_PREP, prev=parse, libs=("util", "tz"), y=Y)
    ap = sheets_append(wf, p + "Sheets Append Callback", A, "callbacks", pc, "cbRow", "K")
    res = wf.prep(p + "Callback Result", JS_CB_RESULT, prev=pc, libs=("util",), y=Y)
    wf.seq(parse, pc, ap, res)
    fo = tool_tail(wf, p, [res], Y)
    c1 = wf.prep(p + "Prep CRM", JS_CB_CRM, prev=fo, libs=("util",), y=Y + 360)
    up = ghl_call(wf, p + "GHL Upsert Contact", A, "POST", "'/contacts/upsert'", c1, body="§.contactBody", y=Y + 360)
    c2 = wf.prep(p + "Prep CRM 2", JS_CB_CRM2, prev=c1, libs=("util",), y=Y + 360)
    tg = ghl_call(wf, p + "GHL Add Tags", A, "POST", "'/contacts/' + §.contactId + '/tags'", c2, body="§.tagBody", y=Y + 360)
    nt = ghl_call(wf, p + "GHL Add Note", A, "POST", "'/contacts/' + §.contactId + '/notes'", c2, body="§.noteBody", y=Y + 360)
    fin = wf.prep(p + "Final Check", JS_CB_FINAL, prev=c2, libs=("util",), y=Y + 360)
    dg = wf.if_(p + "CRM Problem?", "($json.degraded || []).length > 0", y=Y + 360)
    st = wf.stop_error(p + "Raise CRM Alert", "$json.degraded.join(' | ')", y=Y + 360)
    alert = alert_branch(wf, p, fin, A, cfg, Y + 560, "$json.alertNeeded")
    wf.seq(fo, c1, up, c2, tg, nt, fin, dg)
    wf.link(dg, st, 0)
    wf.link(fin, alert)

    # ---- opt_out
    Y = 2000
    p = "[OptOut] "
    parse = tool_head(wf, p, "opt-out", Y, cfg)
    po = wf.prep(p + "Prep Opt-Out", JS_OPT_PREP, prev=parse, libs=("util",), y=Y)
    ap = sheets_append(wf, p + "Sheets Append DNC", A, "dnc", po, "dncRow", "D")
    res = wf.prep(p + "Opt-Out Result", JS_OPT_RESULT, prev=po, libs=("util",), y=Y)
    wf.seq(parse, po, ap, res)
    fo = tool_tail(wf, p, [res], Y)
    optout_effects(wf, p + "CRM ", A, cfg, ctx_node=fo, from_node=fo, y=Y + 360)
    return wf

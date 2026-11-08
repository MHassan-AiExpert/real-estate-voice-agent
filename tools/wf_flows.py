"""01 intake, 03 end-of-call, 04 inbound SMS, 05 callback scheduler, 06 error handler."""
from n8nkit import WF, lib
from wf_common import (config_node, twilio_sms, ghl_call, sheets_append, sheets_read, sheets_url,
                       optout_effects, alert_branch, EXPAND_ALERTS)

# =====================================================================================================
# 01  Lead intake -> outbound call
# =====================================================================================================
JS_NORMALIZE = lib("util", "phone") + r"""
const cfg = $('Config').first().json.cfg;
const body = $('Webhook: Lead Intake').first().json.body;
const rows = Array.isArray(body) ? body : (body && Array.isArray(body.leads) ? body.leads : [body || {}]);
const sd = $getWorkflowStaticData('global');
sd.seen = sd.seen || {};
const now = Date.now();
for (const k of Object.keys(sd.seen)) if (now - sd.seen[k] > 3600000) delete sd.seen[k];
const pick = (o, keys) => { for (const k of keys) { const v = o ? o[k] : undefined; if (v !== undefined && v !== null && String(v).trim() !== '') return String(v).trim(); } return ''; };
const truthy = (v) => ['true', 'yes', 'y', '1', 'on', 'checked', 'agreed'].includes(String(v).toLowerCase().trim());
const slug = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 30);
const out = [];
for (const r0 of rows) {
  const r = r0 || {};
  const c = (r.contact && typeof r.contact === 'object') ? r.contact : r;
  let first = pick(c, ['firstName', 'first_name', 'firstname', 'First Name', 'fname']);
  let last = pick(c, ['lastName', 'last_name', 'lastname', 'Last Name', 'lname']);
  const full = pick(c, ['full_name', 'fullName', 'name', 'Name']);
  if (!first && full) { const parts = full.split(/\s+/); first = parts.shift() || ''; if (!last) last = parts.join(' '); }
  const phone = normalizePhone(pick(c, ['phone', 'phone_number', 'phoneNumber', 'Phone', 'mobile', 'telephone', 'tel']), cfg.agency.default_country_code);
  const email = pick(c, ['email', 'Email', 'email_address']);
  const source = pick(r, ['source', 'lead_source', 'leadSource', 'utm_source', 'campaign']) || pick(c, ['source', 'lead_source']) || 'web form';
  const interest = pick(r, ['interest', 'propertyInterest', 'property_interest', 'property', 'listing', 'listing_address', 'address', 'message', 'notes'])
    .replace(/\s+/g, ' ').replace(/[{}]/g, '').slice(0, 80);
  const consentRaw = pick(r, ['consent', 'consent_given', 'consentToCall', 'call_consent', 'sms_consent', 'tcpa_consent', 'agree', 'agreed_to_contact'])
    || pick(c, ['consent', 'consent_given', 'call_consent', 'sms_consent', 'tcpa_consent']);
  const consent = truthy(consentRaw);
  const contactId = pick(c, ['contact_id', 'contactId']) || pick(r, ['contact_id', 'contactId']);
  let skip = '';
  const tagStr = JSON.stringify(c.tags || r.tags || '').toLowerCase();
  if (!E164.test(phone)) skip = 'invalid_phone';
  else if (/\bdnc\b|do[- ]not[- ]contact|do[- ]not[- ]call/.test(tagStr)) skip = 'dnc_tag';
  else if (cfg.calling.require_consent_flag && !consent) skip = 'no_consent';
  else if (sd.seen[phone] && now - sd.seen[phone] < cfg.calling.dedupe_minutes * 60000) skip = 'duplicate';
  if (!skip) sd.seen[phone] = now;
  out.push({ json: { cfg: cfg, skip: skip, lead: { firstName: first, lastName: last, email: email, phone: phone, source: source, sourceSlug: slug(source), interest: interest, contactId: contactId, consent: consent } }, pairedItem: { item: 0 } });
}
return out;
"""

JS_GATE = lib("util", "phone", "tz") + r"""
const cfg = $('Config').first().json.cfg;
const tz = cfg.agency.timezone;
const leads = $('Normalize Leads').all();
const dncResp = $input.first().json;
const dncFailed = !!(dncResp && dncResp.error);
const dnc = new Set(sheetRows(dncResp).map((r) => normalizePhone(r.phone, cfg.agency.default_country_code)));
const now = Date.now();
const inWin = inWindow(now, cfg.calling.window, tz);
return leads.map((it) => {
  const j = it.json;
  let action = 'call', reason = '', dueMs = 0, warning = '';
  if (j.skip) { action = 'skip'; reason = j.skip; }
  else if (dncFailed) { action = 'defer'; reason = 'dnc_check_failed'; dueMs = now + 10 * 60000; warning = 'DNC sheet could not be read - lead deferred instead of called: ' + errMsg(dncResp); }
  else if (dnc.has(j.lead.phone)) { action = 'skip'; reason = 'dnc'; }
  else if (!inWin) { action = 'defer'; reason = 'outside_hours'; dueMs = nextWindowStart(now, cfg.calling.window, tz); }
  return { json: Object.assign({}, j, { action: action, reason: reason, dueMs: dueMs, gateWarning: warning }), pairedItem: { item: 0 } };
});
"""

JS_SKIP_LOG = r"""
const L = ctx.lead;
ctx.logRow = { values: [[iso(Date.now()), '', 'outbound', L.phone, L.contactId || '', 0, 'skipped:' + ctx.reason, 'skipped', '', '', clip((L.firstName || '') + ' ' + (L.source || ''), 120), '']] };
"""

JS_VAPI_CALL = r"""
const L = ctx.lead;
ctx.vapiBody = {
  assistantId: cfg.vapi.outbound_assistant_id,
  phoneNumberId: cfg.vapi.phone_number_id,
  name: clip('Lead ' + (L.firstName || L.phone), 60),
  customer: { number: L.phone, name: L.firstName || undefined },
  assistantOverrides: { variableValues: {
    firstName: L.firstName || 'there', leadSource: L.source, propertyInterest: L.interest || 'a property',
    contactId: L.contactId || '', attempt: '1' } }
};
"""

JS_CALL_RESULT = r"""
const ok = !resp.error && !!resp.id;
ctx.callPlaced = ok;
ctx.callId = ok ? resp.id : '';
ctx.degraded = ok ? [] : ['Vapi outbound call FAILED for ' + ctx.lead.phone + ' (a retry in 5 minutes was queued): ' + errMsg(resp)];
ctx.needsCallback = !ok;
if (!ok) { ctx.cbDueMs = Date.now() + 5 * 60000; ctx.cbReason = 'call_failed'; }
"""

JS_DEFERRED = r"""
ctx.callPlaced = false;
ctx.callId = '';
ctx.needsCallback = true;
ctx.cbDueMs = ctx.dueMs;
ctx.cbReason = ctx.reason;
ctx.degraded = ctx.gateWarning ? [ctx.gateWarning] : [];
"""

JS_INTAKE_UPSERT = r"""
const L = ctx.lead;
const body = { locationId: cfg.ghl.location_id, phone: L.phone, source: L.source };
if (L.firstName) body.firstName = L.firstName;
if (L.lastName) body.lastName = L.lastName;
if (L.email) body.email = L.email;
body.customFields = cfList(cfg, { ai_lead_source: L.source });
ctx.contactBody = body;
"""

JS_INTAKE_FOLLOW = r"""
const L = ctx.lead;
ctx.contactId = (resp && resp.contact && resp.contact.id) || L.contactId || '';
const nm = ((L.firstName || '') + ' ' + (L.lastName || '')).trim() || L.phone;
ctx.oppBody = { pipelineId: cfg.ghl.pipeline_id, locationId: cfg.ghl.location_id, contactId: ctx.contactId, name: nm + ' - ' + (L.interest || 'enquiry'), status: 'open', pipelineStageId: stageId(cfg, 'new_lead') };
const tags = ['ai-lead', 'source-' + (L.sourceSlug || 'unknown')];
let note;
if (ctx.callPlaced) { tags.push('ai-call-placed'); note = 'AI voice agent started an outbound call within seconds of the enquiry (call ' + ctx.callId + '). Source: ' + L.source + '.'; }
else if (ctx.cbReason === 'call_failed') { tags.push('call-failed'); note = 'The outbound call could NOT be started. A retry is queued for ' + iso(ctx.cbDueMs) + '. Source: ' + L.source + '.'; }
else { tags.push('call-deferred'); note = 'Call deferred (' + ctx.cbReason + '). The AI will call at ' + iso(ctx.cbDueMs) + '. Source: ' + L.source + '.'; }
ctx.tagBody = { tags: tags };
ctx.noteBody = { body: note };
if (ctx.needsCallback) ctx.cbRow = { values: [[mkId(), iso(Date.now()), iso(ctx.cbDueMs), ctx.contactId, L.phone, L.firstName || '', ctx.cbReason, 'ai', 1, 'pending', clip(L.interest || '', 200)]] };
"""

JS_INTAKE_FINAL = r"""
const errs = [];
for (const n of ['GHL Upsert Contact', 'GHL Upsert Opportunity', 'GHL Add Tags', 'GHL Add Note', 'Sheets Append Callback']) { const m = errMsg(safeResp(n)); if (m) errs.push(n + ': ' + m); }
const d = (ctx.degraded || []).slice();
if (errs.length) d.push('Lead intake CRM/queue problem for ' + ctx.lead.phone + ' -> ' + errs.join(' | '));
ctx.degraded = d;
"""


def build_intake(cfg, A, test, tokens):
    wf = WF("01 Lead Intake - Outbound Call", test=test)
    Y = 0
    wf.sticky("## Lead intake -> AI call within seconds\nPOST single lead, an array, or `{leads:[...]}` to the webhook URL (see docs/SETUP.md). "
              "Checks consent, DNC list and calling hours, then asks Vapi to call. CRM updates run AFTER the call is placed.", -40, -260, 700, 150)
    wh = wf.webhook("Webhook: Lead Intake", "lead-intake-" + tokens["intake"], secured=False, x=0, y=Y)
    rs = wf.respond_json("Respond Accepted", "({ ok: true, status: 'accepted' })", y=Y)
    cf = config_node(wf, "Config", cfg, Y)
    nm = wf.code("Normalize Leads", JS_NORMALIZE, y=Y)
    dn = wf.http("Sheets Read DNC", "GET", sheets_url(A, "dnc", "!A1:D5000"), cred="gsheets", ctx=cf,
                 onerr="continueRegularOutput", retries=2, once=True, y=Y)
    gt = wf.code("Gate", JS_GATE, y=Y)
    wf.seq(wh, rs, cf, nm, dn, gt)
    sk = wf.if_("Skip?", "$json.action === 'skip'", y=Y)
    wf.link(gt, sk)
    # skip branch
    psl = wf.prep("Prep Skip Log", JS_SKIP_LOG, prev=None, libs=("util",), y=Y + 260)
    sl = sheets_append(wf, "Sheets Append Skip Log", A, "call_log", psl, "logRow", "L", onerr="continueRegularOutput", retries=2)
    wf.link(sk, psl, 0)
    wf.link(psl, sl)
    # call / defer branch
    cn = wf.if_("Call Now?", "$json.action === 'call'", y=Y - 40)
    wf.link(sk, cn, 1)
    pv = wf.prep("Prep Vapi Call", JS_VAPI_CALL, prev=None, libs=("util",), y=Y - 40)
    vc = wf.http("Vapi Create Call", "POST", "'%s/call'" % A["vapi"], cred="vapi", ctx=pv, body="§.vapiBody",
                 onerr="continueRegularOutput", retries=3, batching=(1, cfg["calling"]["call_interval_ms"]), y=Y - 40)
    mc = wf.prep("Mark Call Result", JS_CALL_RESULT, prev=pv, libs=("util",), y=Y - 40)
    md = wf.prep("Mark Deferred", JS_DEFERRED, prev=None, libs=("util",), x=1500, y=Y + 120)
    wf.link(cn, pv, 0)
    wf.seq(pv, vc, mc)
    wf.link(cn, md, 1)
    # CRM follow-ups (after the call has been placed)
    pu = wf.prep("Prep GHL Upsert", JS_INTAKE_UPSERT, prev=None, libs=("util",), x=2500, y=Y)
    gu = ghl_call(wf, "GHL Upsert Contact", A, "POST", "'/contacts/upsert'", pu, body="§.contactBody")
    pf = wf.prep("Prep Follow-ups", JS_INTAKE_FOLLOW, prev=pu, libs=("util",), y=Y)
    go = ghl_call(wf, "GHL Upsert Opportunity", A, "POST", "'/opportunities/upsert'", pf, body="§.oppBody")
    gtg = ghl_call(wf, "GHL Add Tags", A, "POST", "'/contacts/' + §.contactId + '/tags'", pf, body="§.tagBody")
    gn = ghl_call(wf, "GHL Add Note", A, "POST", "'/contacts/' + §.contactId + '/notes'", pf, body="§.noteBody")
    ic = wf.if_("Needs Callback Row?", "$('Prep Follow-ups').item.json.needsCallback", y=Y)
    sc = sheets_append(wf, "Sheets Append Callback", A, "callbacks", pf, "cbRow", "K", onerr="continueRegularOutput", retries=3)
    fin = wf.prep("Final Check", JS_INTAKE_FINAL, prev=pf, libs=("util",), y=Y)
    dg = wf.if_("Degraded?", "($json.degraded || []).length > 0", y=Y)
    st = wf.stop_error("Raise Alert", "$json.degraded.join(' | ')", y=Y)
    wf.link(mc, pu)
    wf.link(md, pu)
    wf.seq(pu, gu, pf, go, gtg, gn, ic)
    wf.link(ic, sc, 0)
    wf.link(sc, fin)
    wf.link(ic, fin, 1)
    wf.seq(fin, dg)
    wf.link(dg, st, 0)
    return wf


# =====================================================================================================
# 03  End-of-call report
# =====================================================================================================
JS_EOC_PARSE = lib("util", "phone") + r"""
const cfg = $('Config').first().json.cfg;
const body = $('Webhook: Vapi Events').first().json.body || {};
const msg = body.message || {};
if (msg.type !== 'end-of-call-report') return [{ json: { cfg: cfg, skip: true, msgType: msg.type || 'unknown' }, pairedItem: { item: 0 } }];
const call = msg.call || {};
const artifact = msg.artifact || call.artifact || {};
const analysis = msg.analysis || call.analysis || {};
const phoneRaw = (call.customer && call.customer.number) || (msg.customer && msg.customer.number) || '';
const vars = (call.assistantOverrides && call.assistantOverrides.variableValues) || {};
const startedAt = msg.startedAt || call.startedAt || '';
const endedAt = msg.endedAt || call.endedAt || '';
let dur = 0;
if (startedAt && endedAt) dur = Math.max(0, Math.round((Date.parse(endedAt) - Date.parse(startedAt)) / 1000));
if (typeof msg.durationSeconds === 'number') dur = Math.round(msg.durationSeconds);
const transcript = String(artifact.transcript || msg.transcript || '');
const rec = artifact.recording || {};
const recordingUrl = artifact.recordingUrl || (rec.mono && rec.mono.combinedUrl) || rec.stereoUrl || artifact.stereoRecordingUrl || msg.recordingUrl || '';
const endedReason = String(msg.endedReason || call.endedReason || '');
const userSpoke = /(^|\n)\s*(User|Customer|Human)\s*:/i.test(transcript);
const msgsStr = JSON.stringify(artifact.messages || []);
const toolFailure = /"ok\\?"\s*:\s*false/.test(msgsStr);
const r = endedReason.toLowerCase();
let outcome;
if (/voicemail/.test(r)) outcome = 'voicemail';
else if (/did-not-answer|no-answer|busy|rejected|declined/.test(r)) outcome = 'no_answer';
else if (/forwarded|transferred/.test(r)) outcome = 'transferred';
else if (/error|failed|unreachable|invalid|call\.start/.test(r)) outcome = 'error';
else if (userSpoke) outcome = 'conversation';
else outcome = 'silent';
const phone = normalizePhone(phoneRaw, cfg.agency.default_country_code);
return [{ json: {
  cfg: cfg, skip: false, callId: call.id || '', direction: call.type === 'inboundPhoneCall' ? 'inbound' : 'outbound',
  phone: phone, endedReason: endedReason, dur: dur, startedAt: startedAt, endedAt: endedAt, outcome: outcome,
  summary: String(analysis.summary || msg.summary || ''), transcript: transcript, recordingUrl: recordingUrl,
  userSpoke: userSpoke, toolFailure: toolFailure, contactIdVar: vars.contactId || '', firstName: vars.firstName === 'there' ? '' : (vars.firstName || ''),
  interest: vars.propertyInterest || '', attemptVar: vars.attempt || ''
}, pairedItem: { item: 0 } }];
"""

JS_EOC_LOOKUP = r"""
const rows = sheetRows(resp);
const now = Date.now();
ctx.callLogError = errMsg(resp);
ctx.duplicateEvent = !!ctx.callId && rows.some((r) => String(r.call_id) === ctx.callId);
ctx.priorAttempts = rows.filter((r) => String(r.phone) === ctx.phone && String(r.direction) === 'outbound' && String(r.outcome) !== 'skipped'
  && String(r.call_id) !== ctx.callId && Date.parse(r.timestamp) > now - 14 * 86400000).length;
const body = { locationId: cfg.ghl.location_id, phone: ctx.phone };
if (ctx.firstName) body.firstName = ctx.firstName;
ctx.lookupBody = body;
"""

JS_EOC_DECIDE = lib("util", "phone", "tz") + r"""
const prev = $('Prep Lookup').item.json;
const resp = $json;
const ctx = Object.assign({}, prev);
const cfg = ctx.cfg;
const tz = cfg.agency.timezone;
const now = Date.now();
const upsert = safeResp('GHL Upsert Contact') || {};
const contact = (resp && resp.contact) || {};
ctx.contactId = (upsert.contact && upsert.contact.id) || contact.id || ctx.contactIdVar || '';
const idToKey = {};
for (const k of Object.keys(cfg.ghl.custom_fields || {})) idToKey[cfg.ghl.custom_fields[k]] = k;
const f = {};
for (const cf of (contact.customFields || [])) { const k = idToKey[cf.id]; if (k) f[k] = cf.value !== undefined ? cf.value : cf.field_value; }
const tags = (contact.tags || []).map((t) => String(t).toLowerCase());
const outcome = ctx.outcome;
const outbound = ctx.direction === 'outbound';
const qualified = !!ctx.callId && String(f.ai_last_call_id || '') === ctx.callId;
const tier = qualified ? String(f.ai_lead_tier || '') : '';
const score = qualified ? f.ai_lead_score : '';
const viewingBooked = !!ctx.callId && String(f.ai_viewing_call_id || '') === ctx.callId;
const optedOut = String(f.ai_opt_out || '').toLowerCase() === 'yes' || tags.includes('dnc') || contact.dnd === true;
const userLines = (ctx.transcript.match(/(^|\n)\s*User\s*:[^\n]*/gi) || []).join(' ');
const STOP = /\b(stop (calling|contacting|texting|messaging)|don'?t (call|contact|text) me|do not (call|contact|text)|remove (me|my number)|take me off|unsubscribe|no more calls|never call (me )?again|put me on (the |your )?do not call)/i;
const optOutFallback = outcome === 'conversation' && !optedOut && STOP.test(userLines);
const att = (ctx.priorAttempts || 0) + 1;
const labels = { no_answer: 'No answer', voicemail: 'Voicemail', silent: 'Answered, no response', transferred: 'Transferred to an agent', error: 'Call error', conversation: 'Conversation' };
const addTags = ['ai-called'];
const removeTags = [];
let stageKey = '';
let oppStatus = 'open';
let retry = null;
if (optOutFallback) { addTags.push('dnc', 'do-not-contact'); stageKey = 'dnc'; oppStatus = 'lost'; }
else if (!optedOut) {
  if (['no_answer', 'voicemail', 'silent', 'error'].includes(outcome)) {
    if (outbound) {
      stageKey = 'contacted';
      addTags.push(outcome === 'voicemail' ? 'voicemail' : (outcome === 'error' ? 'call-error' : 'no-answer'));
      if (att < cfg.calling.max_attempts) {
        const delays = cfg.calling.retry_delays_minutes;
        const due = nextWindowStart(now + delays[Math.min(att - 1, delays.length - 1)] * 60000, cfg.calling.window, tz);
        retry = { due: due, reason: 'retry_' + outcome, attempt: att + 1 };
        addTags.push('retry-scheduled');
      } else addTags.push('unreachable');
    }
  } else if (outcome === 'conversation') {
    removeTags.push('no-answer', 'voicemail', 'retry-scheduled');
    if (!qualified && !viewingBooked) { addTags.push('needs-review'); stageKey = outbound ? 'contacted' : 'new_lead'; }
  } else if (outcome === 'transferred') { addTags.push('transferred-to-agent'); }
}
const name = [contact.firstName, contact.lastName].filter(Boolean).join(' ') || ctx.firstName || 'Lead';
const link = ghlLink(cfg, ctx.contactId);
const alerts = [];
if (tier === 'hot' && !optedOut) alerts.push('HOT LEAD call summary - ' + name + ' ' + ctx.phone + ': ' + clip(ctx.summary, 220) + ' ' + link);
if (outcome === 'transferred') alerts.push('Transferred call finished - ' + name + ' ' + ctx.phone + ': ' + clip(ctx.summary, 220) + ' ' + link);
if (ctx.toolFailure) { alerts.push('A tool failed during the AI call with ' + name + ' ' + ctx.phone + ' - please follow up manually. ' + link); addTags.push('needs-human-followup'); }
if (optOutFallback) alerts.push('ADMIN: opt-out wording heard from ' + ctx.phone + ' but the opt_out tool was not called. Number was suppressed automatically - please review. ' + link);
const numbers = (cfg.agents.alert_sms_numbers || []).slice();
if ((optOutFallback || ctx.toolFailure) && cfg.admin.alert_sms_number && !numbers.includes(cfg.admin.alert_sms_number)) numbers.push(cfg.admin.alert_sms_number);
ctx.alertNeeded = alerts.length > 0;
ctx.alertText = clip(alerts.join(' || '), 600);
ctx.alertNumbers = numbers;
ctx.slackUrl = cfg.agents.slack_webhook_url || '';
ctx.tier = tier; ctx.score = score; ctx.qualified = qualified; ctx.viewingBooked = viewingBooked; ctx.optedOut = optedOut; ctx.optOutFallback = optOutFallback;
ctx.outcomeLabel = labels[outcome] || outcome;
ctx.attempt = att;
const body = { locationId: cfg.ghl.location_id, phone: ctx.phone };
if (optOutFallback) body.dnd = true;
body.customFields = cfList(cfg, {
  ai_call_summary: clip(ctx.summary, 1500), ai_last_call_at: ctx.startedAt || iso(now),
  ai_call_attempts: outbound ? att : undefined, ai_opt_out: optOutFallback ? 'Yes' : undefined,
  ai_lead_source: (!outbound && !f.ai_lead_source) ? 'Inbound call' : undefined
});
ctx.contactBody = body;
const lines = ['AI CALL (' + ctx.direction + ') - ' + ctx.outcomeLabel + (tier ? ' - ' + tier.toUpperCase() + (score !== '' ? ' (' + score + ')' : '') : ''),
  'Duration: ' + ctx.dur + 's | Ended: ' + (ctx.endedReason || 'n/a') + (outbound ? ' | Attempt ' + att : ''),
  viewingBooked ? 'Viewing booked: ' + (f.ai_viewing_datetime || 'yes') : '',
  optOutFallback ? 'OPT-OUT detected from transcript - number suppressed.' : '',
  ctx.toolFailure ? 'WARNING: a tool call failed during this call.' : '',
  retry ? 'Retry scheduled: ' + iso(retry.due) : '',
  '', 'SUMMARY: ' + (ctx.summary || '(none)'), ctx.recordingUrl ? 'RECORDING: ' + ctx.recordingUrl : '', '', '--- TRANSCRIPT ---', clip(ctx.transcript, 8000)];
ctx.noteBody = { body: lines.filter((l, i) => l !== '' || i > 0).join('\n') };
ctx.tagBody = { tags: addTags };
ctx.removeTags = { tags: removeTags };
ctx.hasRemove = removeTags.length > 0;
ctx.hasStage = !!stageKey;
ctx.oppBody = { pipelineId: cfg.ghl.pipeline_id, locationId: cfg.ghl.location_id, contactId: ctx.contactId, name: name + ' - ' + (ctx.interest || 'enquiry'), status: oppStatus, pipelineStageId: stageId(cfg, stageKey) };
ctx.hasRetry = !!retry;
ctx.cbRow = retry ? { values: [[mkId(), iso(now), iso(retry.due), ctx.contactId, ctx.phone, ctx.firstName || '', retry.reason, 'ai', retry.attempt, 'pending', clip(ctx.interest || '', 200)]] } : null;
ctx.dncRow = { values: [[ctx.phone, iso(now), 'voice:transcript-fallback', 'opt-out wording in call ' + ctx.callId]] };
ctx.logRow = { values: [[ctx.startedAt || iso(now), ctx.callId, ctx.direction, ctx.phone, ctx.contactId, ctx.dur, ctx.endedReason, outcome, tier, score, clip(ctx.summary, 500), ctx.recordingUrl]] };
return { json: ctx };
"""

JS_EOC_FINAL = r"""
const errs = [];
for (const n of ['GHL Update Contact', 'GHL Add Note', 'GHL Add Tags', 'GHL Remove Tags', 'GHL Upsert Opportunity', 'Sheets Append Callback', 'Sheets Append DNC', 'Sheets Append CallLog']) {
  const m = errMsg(safeResp(n)); if (m) errs.push(n + ': ' + m);
}
ctx.degraded = errs.length ? ['End-of-call sync problem for call ' + ctx.callId + ' (' + ctx.phone + ') -> ' + errs.join(' | ')] : [];
"""


def build_eoc(cfg, A, test):
    wf = WF("03 Vapi End-of-Call Report", test=test)
    Y = 0
    D = "$('Decide').item.json"
    wf.sticky("## End-of-call report -> CRM\nVapi posts `end-of-call-report` here. Writes summary + transcript to GoHighLevel, sets stage/tags, "
              "schedules retries for no-answer/voicemail, catches opt-outs the agent missed, alerts agents for hot leads.", -40, -260, 700, 150)
    wh = wf.webhook("Webhook: Vapi Events", "vapi-events", secured=True, x=0, y=Y)
    rs = wf.respond_json("Respond OK", "({ ok: true })", y=Y)
    cf = config_node(wf, "Config", cfg, Y)
    pa = wf.code("Parse Event", JS_EOC_PARSE, y=Y)
    sk = wf.if_("Other Event Type?", "$json.skip", y=Y)
    ig = wf.noop("Ignore Other Events", y=Y + 200)
    rd = wf.http("Sheets Read CallLog", "GET", sheets_url(A, "call_log", "!A1:L5000"), cred="gsheets", ctx=pa,
                 onerr="continueRegularOutput", retries=2, y=Y)
    pl = wf.prep("Prep Lookup", JS_EOC_LOOKUP, prev=pa, libs=("util",), y=Y)
    dup = wf.if_("Duplicate Event?", "$json.duplicateEvent", y=Y)
    ig2 = wf.noop("Ignore Duplicate", y=Y + 200)
    up = wf.http("GHL Upsert Contact", "POST", "'%s/contacts/upsert'" % A["ghl"], cred="ghl", ctx=pl, body="§.lookupBody",
                 onerr="continueRegularOutput", retries=3, y=Y)
    gc = wf.http("GHL Get Contact", "GET", "'%s/contacts/' + ($json.contact && $json.contact.id ? $json.contact.id : 'unknown')" % A["ghl"],
                 cred="ghl", onerr="continueRegularOutput", retries=3, y=Y)
    dc = wf.code("Decide", JS_EOC_DECIDE, each=True, y=Y)
    wf.seq(wh, rs, cf, pa, sk)
    wf.link(sk, ig, 0)
    wf.link(sk, rd, 1)
    wf.seq(rd, pl, dup)
    wf.link(dup, ig2, 0)
    wf.link(dup, up, 1)
    wf.seq(up, gc, dc)
    # writes
    Y2 = Y + 420
    uc = wf.http("GHL Update Contact", "POST", "'%s/contacts/upsert'" % A["ghl"], cred="ghl", ctx=dc, body="§.contactBody",
                 onerr="continueRegularOutput", retries=3, x=0, y=Y2)
    nt = ghl_call(wf, "GHL Add Note", A, "POST", "'/contacts/' + §.contactId + '/notes'", dc, body="§.noteBody")
    tg = ghl_call(wf, "GHL Add Tags", A, "POST", "'/contacts/' + §.contactId + '/tags'", dc, body="§.tagBody")
    ir = wf.if_("Remove Tags?", D + ".hasRemove", y=Y2)
    rm = ghl_call(wf, "GHL Remove Tags", A, "DELETE", "'/contacts/' + §.contactId + '/tags'", dc, body="§.removeTags")
    j1 = wf.noop("Join 1", y=Y2)
    is_ = wf.if_("Stage Update?", D + ".hasStage", y=Y2)
    op = ghl_call(wf, "GHL Upsert Opportunity", A, "POST", "'/opportunities/upsert'", dc, body="§.oppBody")
    j2 = wf.noop("Join 2", y=Y2)
    wf.seq(dc, uc, nt, tg, ir)
    wf.link(ir, rm, 0)
    wf.link(rm, j1)
    wf.link(ir, j1, 1)
    wf.link(j1, is_)
    wf.link(is_, op, 0)
    wf.link(op, j2)
    wf.link(is_, j2, 1)
    Y3 = Y2 + 240
    ire = wf.if_("Retry?", D + ".hasRetry", x=0, y=Y3)
    sc = sheets_append(wf, "Sheets Append Callback", A, "callbacks", dc, "cbRow", "K", onerr="continueRegularOutput", retries=3)
    j3 = wf.noop("Join 3", y=Y3)
    iop = wf.if_("Opt-Out Fallback?", D + ".optOutFallback", y=Y3)
    sd = sheets_append(wf, "Sheets Append DNC", A, "dnc", dc, "dncRow", "D", onerr="continueRegularOutput", retries=3)
    j4 = wf.noop("Join 4", y=Y3)
    sl = sheets_append(wf, "Sheets Append CallLog", A, "call_log", dc, "logRow", "L", onerr="continueRegularOutput", retries=3)
    fin = wf.prep("Final Check", JS_EOC_FINAL, prev=dc, libs=("util",), y=Y3)
    wf.link(j2, ire)
    wf.link(ire, sc, 0)
    wf.link(sc, j3)
    wf.link(ire, j3, 1)
    wf.link(j3, iop)
    wf.link(iop, sd, 0)
    wf.link(sd, j4)
    wf.link(iop, j4, 1)
    wf.seq(j4, sl, fin)
    # alerts
    Y4 = Y3 + 240
    dg = wf.if_("Degraded?", "($json.degraded || []).length > 0", x=0, y=Y4)
    st = wf.stop_error("Raise Alert", "$json.degraded.join(' | ')", y=Y4)
    wf.link(fin, dg)
    wf.link(dg, st, 0)
    alert = alert_branch(wf, "", "Final Check", A, cfg, Y4 + 200, "$json.alertNeeded")
    wf.link(fin, alert)
    isl = wf.if_("Slack Alert?", "$json.alertNeeded && $json.slackUrl", x=0, y=Y4 + 400)
    sl2 = wf.http("Slack Alert", "POST", "§.slackUrl", ctx=fin, body="({ text: §.alertText })", onerr="continueRegularOutput", retries=2, y=Y4 + 400)
    wf.link(fin, isl)
    wf.link(isl, sl2, 0)
    return wf


# =====================================================================================================
# 04  Inbound SMS (Twilio) -> STOP handling
# =====================================================================================================
JS_SMS_PARSE = lib("util", "phone") + r"""
const cfg = $('Config').first().json.cfg;
const b = $('Webhook: Twilio SMS').first().json.body || {};
const phone = normalizePhone(b.From, cfg.agency.default_country_code);
const text = String(b.Body || '').trim();
const kw = text.toLowerCase().replace(/[^a-z ]/g, '').trim();
const words = ['stop', 'stopall', 'unsubscribe', 'cancel', 'end', 'quit', 'revoke', 'optout', 'opt out'];
const phrase = /\b(stop (calling|texting|messaging|contacting)|do not (call|text|contact)|don'?t (call|text|contact)|remove me|take me off|unsubscribe)\b/i;
const isOptOut = b.OptOutType === 'STOP' || words.includes(kw) || phrase.test(text);
return [{ json: { cfg: cfg, phone: phone, text: text, isOptOut: isOptOut, direction: 'sms', callId: '', args: {}, contactId: '' }, pairedItem: { item: 0 } }];
"""

JS_SMS_OPT_PREP = r"""
ctx.optReason = 'sms_stop';
ctx.optSource = 'inbound SMS';
ctx.dncRow = { values: [[ctx.phone, iso(Date.now()), 'sms', 'STOP: ' + clip(ctx.text, 40)]] };
"""

JS_SMS_REPLY_CRM = r"""
ctx.contactBody = { locationId: cfg.ghl.location_id, phone: ctx.phone };
"""

JS_SMS_REPLY_CRM2 = r"""
ctx.contactId = (resp && resp.contact && resp.contact.id) || '';
ctx.noteBody = { body: 'Inbound SMS from ' + ctx.phone + ': ' + clip(ctx.text, 500) };
ctx.tagBody = { tags: ['sms-reply'] };
ctx.alertNeeded = !!cfg.agents.alert_on_sms_reply;
ctx.alertNumbers = cfg.agents.alert_sms_numbers || [];
ctx.alertText = clip('SMS reply from ' + ctx.phone + ': "' + clip(ctx.text, 160) + '" ' + ghlLink(cfg, ctx.contactId), 450);
"""


def build_sms(cfg, A, test, tokens):
    wf = WF("04 Twilio Inbound SMS - Opt-Out Handler", test=test)
    Y = 0
    wf.sticky("## Inbound SMS\nSTOP-style replies add the number to the DNC sheet, set Do-Not-Disturb in GoHighLevel, move the opportunity "
              "to Do Not Contact and cancel pending AI callbacks. Other replies are logged to the contact and forwarded to agents. "
              "Twilio itself also blocks further texts to a number that replies STOP.", -40, -260, 700, 150)
    wh = wf.webhook("Webhook: Twilio SMS", "twilio-sms-" + tokens["twilio"], secured=False, x=0, y=Y)
    rs = wf.respond_text("Respond Empty TwiML", "<Response></Response>", y=Y)
    cf = config_node(wf, "Config", cfg, Y)
    pa = wf.code("Parse SMS", JS_SMS_PARSE, y=Y)
    io = wf.if_("Opt-Out Message?", "$json.isOptOut", y=Y)
    wf.seq(wh, rs, cf, pa, io)
    # opt-out
    po = wf.prep("Prep Opt-Out", JS_SMS_OPT_PREP, prev=pa, libs=("util",), y=Y - 200)
    sd = sheets_append(wf, "Sheets Append DNC", A, "dnc", po, "dncRow", "D")
    wf.link(io, po, 0)
    wf.link(po, sd)
    optout_effects(wf, "CRM ", A, cfg, ctx_node=po, from_node=sd, y=Y - 200)
    # reply
    pr = wf.prep("Prep Reply", JS_SMS_REPLY_CRM, prev=pa, libs=("util",), y=Y + 240)
    gu = ghl_call(wf, "GHL Upsert Contact", A, "POST", "'/contacts/upsert'", pr, body="§.contactBody")
    pr2 = wf.prep("Prep Reply 2", JS_SMS_REPLY_CRM2, prev=pr, libs=("util",), y=Y + 240)
    gn = ghl_call(wf, "GHL Add Note", A, "POST", "'/contacts/' + §.contactId + '/notes'", pr2, body="§.noteBody")
    gt = ghl_call(wf, "GHL Add Tags", A, "POST", "'/contacts/' + §.contactId + '/tags'", pr2, body="§.tagBody")
    wf.link(io, pr, 1)
    wf.seq(pr, gu, pr2, gn, gt)
    al = alert_branch(wf, "Reply ", "Prep Reply 2", A, cfg, Y + 440, "$('Prep Reply 2').item.json.alertNeeded")
    wf.link(gt, al)
    return wf


# =====================================================================================================
# 05  Callback scheduler
# =====================================================================================================
JS_SCHED_WINDOW = lib("util", "tz") + r"""
const cfg = $('Config').first().json.cfg;
if (!inWindow(Date.now(), cfg.calling.window, cfg.agency.timezone)) return [];
return [{ json: { cfg: cfg } }];
"""

JS_SCHED_SELECT = lib("util", "phone") + r"""
const cfg = $('Config').first().json.cfg;
const dncResp = $input.first().json;
const cbResp = $('Sheets Read Callbacks').first().json;
const sd = $getWorkflowStaticData('global');
const now = Date.now();
if ((dncResp && dncResp.error) || (cbResp && cbResp.error)) {
  if (!sd.lastReadAlert || now - sd.lastReadAlert > 3600000) { sd.lastReadAlert = now; throw new Error('Callback scheduler paused: could not read the Callbacks/DNC sheet. ' + errMsg(dncResp) + ' ' + errMsg(cbResp)); }
  return [];
}
const dnc = new Set(sheetRows(dncResp).map((r) => normalizePhone(r.phone, cfg.agency.default_country_code)));
const out = [];
let dials = 0;
const mk = (r, phone, action, newStatus, note) => ({ json: {
  cfg: cfg, row: r._row, action: action, newStatus: newStatus, note: note, phone: phone,
  vapiBody: action === 'dial' ? {
    assistantId: cfg.vapi.outbound_assistant_id, phoneNumberId: cfg.vapi.phone_number_id, name: clip('Callback ' + (r.first_name || phone), 60),
    customer: { number: phone, name: r.first_name || undefined },
    assistantOverrides: { variableValues: { firstName: r.first_name || 'there', leadSource: 'a callback you asked for', propertyInterest: r.note || 'your recent enquiry', contactId: String(r.contact_id || ''), attempt: String(r.attempt || 2) } }
  } : null
}, pairedItem: { item: 0 } });
for (const r of sheetRows(cbResp)) {
  if (String(r.status).toLowerCase() !== 'pending') continue;
  if (String(r.handler).toLowerCase() !== 'ai') continue;
  const due = Date.parse(r.due_at);
  if (isNaN(due) || due > now) continue;
  const phone = normalizePhone(r.phone, cfg.agency.default_country_code);
  if (!E164.test(phone)) { out.push(mk(r, phone, 'skip', 'failed', 'invalid phone')); continue; }
  if (dnc.has(phone)) { out.push(mk(r, phone, 'skip', 'cancelled', 'on DNC list')); continue; }
  if (dials >= cfg.calling.scheduler_max_per_run) break;
  dials++;
  out.push(mk(r, phone, 'dial', 'calling', 'dialing ' + iso(now)));
}
return out;
"""

JS_SCHED_RESULT = r"""
const ok = !resp.error && !!resp.id;
ctx.finalStatus = ok ? 'done' : 'failed';
ctx.finalNote = ok ? ('call ' + resp.id) : ('vapi error: ' + errMsg(resp));
ctx.degraded = ok ? [] : ['Scheduled callback call FAILED for ' + ctx.phone + ' (row ' + ctx.row + '): ' + errMsg(resp)];
"""


def build_scheduler(cfg, A, test):
    wf = WF("05 Callback Scheduler", test=test)
    Y = 0
    wf.sticky("## Callback scheduler\nEvery %d min (inside calling hours) picks due rows from the **Callbacks** sheet "
              "(handler = ai, status = pending), re-checks the DNC sheet, then places the call through Vapi. "
              "Retries for no-answer/voicemail, 'call me later' requests and out-of-hours leads all land here." % cfg["calling"]["scheduler_interval_minutes"],
              -40, -260, 700, 150)
    sc = wf.schedule("Every Few Minutes", cfg["calling"]["scheduler_interval_minutes"], x=0, y=Y)
    cf = config_node(wf, "Config", cfg, Y)
    cw = wf.code("Check Calling Window", JS_SCHED_WINDOW, y=Y)
    rc = wf.http("Sheets Read Callbacks", "GET", sheets_url(A, "callbacks", "!A1:K5000"), cred="gsheets", ctx=cf,
                 onerr="continueRegularOutput", retries=2, once=True, y=Y)
    rd = wf.http("Sheets Read DNC", "GET", sheets_url(A, "dnc", "!A1:D5000"), cred="gsheets", ctx=cf,
                 onerr="continueRegularOutput", retries=2, once=True, y=Y)
    se = wf.code("Select Due", JS_SCHED_SELECT, y=Y)
    ms = wf.http("Mark Status", "PUT",
                 "'%s/' + §.cfg.google.spreadsheet_id + '/values/' + encodeURIComponent(§.cfg.google.tabs.callbacks + '!J' + §.row + ':K' + §.row) + '?valueInputOption=RAW'" % A["sheets"],
                 cred="gsheets", ctx=se, body="({ values: [[§.newStatus, §.note]] })", retries=3, y=Y)
    idl = wf.if_("Dial?", "$('Select Due').item.json.action === 'dial'", y=Y)
    vc = wf.http("Vapi Create Call", "POST", "'%s/call'" % A["vapi"], cred="vapi", ctx=se, body="§.vapiBody",
                 onerr="continueRegularOutput", retries=3, batching=(1, cfg["calling"]["call_interval_ms"]), y=Y)
    pr = wf.prep("Prep Call Result", JS_SCHED_RESULT, prev=se, libs=("util",), y=Y)
    mr = wf.http("Mark Result", "PUT",
                 "'%s/' + §.cfg.google.spreadsheet_id + '/values/' + encodeURIComponent(§.cfg.google.tabs.callbacks + '!J' + §.row + ':K' + §.row) + '?valueInputOption=RAW'" % A["sheets"],
                 cred="gsheets", ctx=pr, body="({ values: [[§.finalStatus, §.finalNote]] })", retries=3, y=Y)
    dg = wf.if_("Degraded?", "($('Prep Call Result').item.json.degraded || []).length > 0", y=Y)
    st = wf.stop_error("Raise Alert", "$('Prep Call Result').item.json.degraded.join(' | ')", y=Y)
    wf.seq(sc, cf, cw, rc, rd, se, ms, idl)
    wf.link(idl, vc, 0)
    wf.seq(vc, pr, mr, dg)
    wf.link(dg, st, 0)
    return wf


# =====================================================================================================
# 06  Error handler
# =====================================================================================================
JS_ERR_FORMAT = lib("util") + r"""
const cfg = $('Config').first().json.cfg;
const e = $('Error Trigger').first().json || {};
const wfName = (e.workflow && e.workflow.name) || 'unknown workflow';
const ex = e.execution || {};
const msg = (ex.error && ex.error.message) || 'unknown error';
const node = ex.lastNodeExecuted || (ex.error && ex.error.node && ex.error.node.name) || '';
const text = clip('Voice agent alert: ' + wfName + (node ? ' / ' + node : '') + ' - ' + msg + (ex.url ? ' ' + ex.url : ''), 450);
return [{ json: { cfg: cfg, text: text, smsBody: text, smsTo: cfg.admin.alert_sms_number || '', slackUrl: cfg.admin.slack_webhook_url || '' } }];
"""


def build_error(cfg, A, test):
    wf = WF("06 Error Handler", test=test)
    Y = 0
    wf.sticky("## Error handler\nSet this as the **Error workflow** (Workflow settings) of workflows 01-05. "
              "Sends an SMS and/or Slack message to the admin when any execution fails or raises a degraded-service alert.", -40, -260, 700, 120)
    et = wf.error_trigger("Error Trigger", x=0, y=Y)
    cf = config_node(wf, "Config", cfg, Y)
    fm = wf.code("Format Error", JS_ERR_FORMAT, y=Y)
    ia = wf.if_("Admin SMS Set?", "$json.smsTo", y=Y)
    sm = twilio_sms(wf, "Twilio SMS Admin", A, cfg, ctx=fm, retries=2)
    isl = wf.if_("Slack Set?", "$json.slackUrl", x=1000, y=Y + 200)
    sl = wf.http("Slack Alert", "POST", "§.slackUrl", ctx=fm, body="({ text: §.text })", onerr="continueRegularOutput", retries=2, y=Y + 200)
    wf.seq(et, cf, fm)
    wf.link(fm, ia)
    wf.link(ia, sm, 0)
    wf.link(fm, isl)
    wf.link(isl, sl, 0)
    return wf

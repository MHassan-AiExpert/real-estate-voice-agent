"""Shared building blocks for the workflow generators."""
import json
from n8nkit import lib

PARSE_TOOL = lib("util", "phone") + r"""
const cfg = $('__CONFIG__').first().json.cfg;
const req = $('__WEBHOOK__').first().json;
const body = req.body || {};
const msg = body.message || body;
const call = msg.call || {};
const list = msg.toolCallList || msg.toolCalls || [];
const phoneRaw = (call.customer && call.customer.number) || (msg.customer && msg.customer.number) || '';
const vars = (call.assistantOverrides && call.assistantOverrides.variableValues) || {};
const direction = call.type === 'inboundPhoneCall' ? 'inbound' : 'outbound';
const out = [];
for (const tc of list) {
  const fn = tc.function || {};
  let args = fn.arguments !== undefined ? fn.arguments : (tc.arguments !== undefined ? tc.arguments : (tc.parameters || {}));
  if (typeof args === 'string') { try { args = JSON.parse(args); } catch (e) { args = {}; } }
  out.push({ json: {
    cfg: cfg, toolCallId: tc.id, toolName: fn.name || tc.name || '', args: args || {},
    callId: call.id || '', direction: direction,
    phone: normalizePhone(phoneRaw, cfg.agency.default_country_code),
    contactId: vars.contactId || '', firstNameVar: vars.firstName || '', attemptVar: vars.attempt || ''
  } });
}
if (!out.length) out.push({ json: { cfg: cfg, noCalls: true, toolCallId: '', toolName: '', args: {}, phone: '' } });
return out;
"""

BUILD_RESPONSE = r"""
const results = [];
const degraded = [];
for (const it of $input.all()) {
  const j = it.json;
  if (j.vapiResult) results.push(j.vapiResult);
  if (Array.isArray(j.degraded)) for (const d of j.degraded) degraded.push(d);
}
return [{ json: { response: { results: results }, degraded: degraded } }];
"""


def fan_out_js(result_nodes):
    names = json.dumps(result_nodes)
    return ("const names = " + names + ";\n"
            "const out = [];\n"
            "for (const n of names) {\n"
            "  try {\n"
            "    const node = $(n);\n"
            "    if (node.isExecuted === false) continue;\n"
            "    for (const it of node.all()) { if (it.json.sideEffects) out.push({ json: it.json }); }\n"
            "  } catch (e) { /* node did not run for this request */ }\n"
            "}\n"
            "return out;\n")


def config_node(wf, name, cfg_obj, y):
    js = ("// CONFIG (generated from config.json by tools/build_workflows.py).\n"
          "// Values can be edited here directly in n8n; re-running the build overwrites them.\n"
          "return [{ json: { cfg: " + json.dumps(cfg_obj, indent=2) + " } }];\n")
    return wf.code(name, js, y=y)


def tool_head(wf, p, tool, y, cfg_obj):
    """Webhook -> Config -> Parse Tool Call. Returns the Parse node name."""
    wh = wf.webhook(p + "Webhook", "vapi-tool-" + tool, x=0, y=y)
    cf = config_node(wf, p + "Config", cfg_obj, y)
    js = PARSE_TOOL.replace("__CONFIG__", cf).replace("__WEBHOOK__", wh)
    parse = wf.code(p + "Parse Tool Call", js, y=y)
    wf.seq(wh, cf, parse)
    return parse


def tool_tail(wf, p, results, y):
    """Result nodes -> Build Response -> Respond to Vapi (-> alert if degraded, -> Fan Out for side effects)."""
    br = wf.code(p + "Build Response", BUILD_RESPONSE, y=y)
    for r in results:
        wf.link(r, br)
    rs = wf.respond_json(p + "Respond to Vapi", "$json.response", y=y)
    wf.link(br, rs)
    dg = wf.if_(p + "Degraded?", "($json.degraded || []).length > 0", y=y - 120)
    stop = wf.stop_error(p + "Raise Alert", "'Voice agent tool problem: ' + $json.degraded.join(' | ')", y=y - 120)
    wf.link(rs, dg)
    wf.link(dg, stop, 0)
    fo = wf.code(p + "Fan Out", fan_out_js(results), y=y + 120)
    wf.link(rs, fo)
    return fo


def sms_params(cfg, ctx_ref_to, ctx_ref_body):
    form = {"To": ctx_ref_to, "Body": ctx_ref_body}
    if cfg["twilio"].get("messaging_service_sid"):
        form["MessagingServiceSid"] = "§.cfg.twilio.messaging_service_sid"
    else:
        form["From"] = "§.cfg.twilio.from_number"
    return form


def twilio_sms(wf, name, A, cfg, ctx, to="§.smsTo", body="§.smsBody", onerr="continueRegularOutput", retries=2, **kw):
    return wf.http(name, "POST",
                   "'%s/2010-04-01/Accounts/' + §.cfg.twilio.account_sid + '/Messages.json'" % A["twilio"],
                   cred="twilio", ctx=ctx, form=sms_params(cfg, to, body), onerr=onerr, retries=retries, **kw)


def ghl_call(wf, name, A, method, path_expr, ctx, body=None, onerr="continueRegularOutput", retries=3, **kw):
    return wf.http(name, method, "'%s' + %s" % (A["ghl"], path_expr), cred="ghl", ctx=ctx, body=body,
                   onerr=onerr, retries=retries, **kw)


def sheets_url(A, tab_key, suffix):
    """JS expression for a Sheets values URL. suffix examples: '!A1:K2000' or '!A:K:append?...'"""
    return ("'%s/' + §.cfg.google.spreadsheet_id + '/values/' + encodeURIComponent(§.cfg.google.tabs.%s + '%s')"
            % (A["sheets"], tab_key, suffix))


def sheets_append(wf, name, A, tab_key, ctx, body_field, cols, onerr=None, retries=3):
    rng = "!A:" + cols
    url = ("'%s/' + §.cfg.google.spreadsheet_id + '/values/' + encodeURIComponent(§.cfg.google.tabs.%s + '%s')"
           " + ':append?valueInputOption=RAW&insertDataOption=INSERT_ROWS'" % (A["sheets"], tab_key, rng))
    return wf.http(name, "POST", url, cred="gsheets", ctx=ctx, body="§." + body_field, onerr=onerr, retries=retries)


def sheets_read(wf, name, A, tab_key, rng, ctx, onerr="continueRegularOutput", once=False):
    return wf.http(name, "GET", sheets_url(A, tab_key, rng), cred="gsheets", ctx=ctx, onerr=onerr, retries=2, once=once)


EXPAND_ALERTS = r"""
const base = $('__CTX__').first().json;
return (base.alertNumbers || []).filter(Boolean).map((n) => ({ json: { cfg: base.cfg, smsTo: n, smsBody: base.alertText } }));
"""


def alert_branch(wf, p, ctx_node, A, cfg, y, cond_expr):
    """IF cond -> Expand Alerts -> Twilio SMS Alert. Returns the IF node (link into it)."""
    iff = wf.if_(p + "Alert Needed?", cond_expr, y=y)
    ex = wf.code(p + "Expand Alerts", EXPAND_ALERTS.replace("__CTX__", ctx_node), y=y)
    sms = twilio_sms(wf, p + "Twilio SMS Alert", A, cfg, ctx=ex)
    wf.link(iff, ex, 0)
    wf.link(ex, sms)
    return iff


# --- Opt-out side effects (shared by the opt_out tool and inbound SMS STOP) -------------------
JS_OPT_CRM = r"""
const body = { locationId: cfg.ghl.location_id, phone: ctx.phone, dnd: true };
body.customFields = cfList(cfg, { ai_opt_out: 'Yes' });
ctx.contactBody = body;
"""

JS_OPT_CRM2 = r"""
ctx.contactId = (resp && resp.contact && resp.contact.id) || ctx.contactId || '';
ctx.tagBody = { tags: ['dnc', 'do-not-contact'] };
ctx.oppBody = { pipelineId: cfg.ghl.pipeline_id, locationId: cfg.ghl.location_id, contactId: ctx.contactId, name: 'Do Not Contact', status: 'lost', pipelineStageId: stageId(cfg, 'dnc') };
ctx.noteBody = { body: 'OPT-OUT recorded (' + ctx.optSource + '). Reason: ' + ctx.optReason + '. Phone ' + ctx.phone + ' added to the suppression list; calls and texts are blocked. Pending AI callbacks cancelled.' };
"""

JS_OPT_CANCEL = r"""
const rows = sheetRows(resp).filter((r) => normalizePhone(r.phone, cfg.agency.default_country_code) === ctx.phone && String(r.status).toLowerCase() === 'pending');
ctx.cancelCount = rows.length;
ctx.cancelBody = { valueInputOption: 'RAW', data: rows.map((r) => ({ range: cfg.google.tabs.callbacks + '!J' + r._row + ':K' + r._row, values: [['cancelled', 'opt-out']] })) };
"""

JS_OPT_FINAL = r"""
const P = '__P__';
const names = ['GHL Upsert Contact', 'GHL Add Tags', 'GHL Upsert Opportunity', 'GHL Add Note', 'Sheets Read Callbacks', 'Sheets Cancel Callbacks'];
const errs = [];
for (const n of names) { const m = errMsg(safeResp(P + n)); if (m) errs.push(n + ': ' + m); }
ctx.degraded = errs.length ? ['Opt-out CRM sync problem for ' + ctx.phone + ' (the number IS on the suppression sheet): ' + errs.join(' | ')] : [];
"""


def optout_effects(wf, p, A, cfg, ctx_node, from_node, y):
    """From `from_node`: GHL dnd + tag + stage, cancel pending callbacks, alert on failure."""
    c1 = wf.prep(p + "Prep CRM", JS_OPT_CRM, prev=ctx_node, libs=("util",), y=y)
    up = ghl_call(wf, p + "GHL Upsert Contact", A, "POST", "'/contacts/upsert'", c1, body="§.contactBody")
    c2 = wf.prep(p + "Prep CRM 2", JS_OPT_CRM2, prev=c1, libs=("util",), y=y)
    tg = ghl_call(wf, p + "GHL Add Tags", A, "POST", "'/contacts/' + §.contactId + '/tags'", c2, body="§.tagBody")
    op = ghl_call(wf, p + "GHL Upsert Opportunity", A, "POST", "'/opportunities/upsert'", c2, body="§.oppBody")
    nt = ghl_call(wf, p + "GHL Add Note", A, "POST", "'/contacts/' + §.contactId + '/notes'", c2, body="§.noteBody")
    rd = sheets_read(wf, p + "Sheets Read Callbacks", A, "callbacks", "!A1:K2000", c2)
    cn = wf.prep(p + "Prep Cancel", JS_OPT_CANCEL, prev=c2, libs=("util", "phone"), y=y)
    iff = wf.if_(p + "Any Callbacks To Cancel?", "$json.cancelCount > 0", y=y)
    cx = wf.http(p + "Sheets Cancel Callbacks", "POST", "'%s/' + §.cfg.google.spreadsheet_id + '/values:batchUpdate'" % A["sheets"],
                 cred="gsheets", ctx=cn, body="§.cancelBody", onerr="continueRegularOutput", retries=2)
    fin = wf.prep(p + "Final Check", JS_OPT_FINAL.replace("__P__", p), prev=cn, libs=("util",), y=y)
    dg = wf.if_(p + "Degraded?", "($json.degraded || []).length > 0", y=y)
    stop = wf.stop_error(p + "Raise Alert", "$json.degraded.join(' | ')", y=y)
    wf.link(from_node, c1)
    wf.seq(c1, up, c2, tg, op, nt, rd, cn, iff)
    wf.link(iff, cx, 0)
    wf.link(cx, fin)
    wf.link(iff, fin, 1)
    wf.seq(fin, dg)
    wf.link(dg, stop, 0)
    return fin

#!/usr/bin/env python3
"""Drive the workflows running in a local n8n (tests/e2e/run_n8n.sh) with realistic payloads and assert on what the
mock GoHighLevel / Vapi / Twilio / Calendar / Sheets servers received.   usage: run_scenarios.py <config.test.json>"""
import json, random, sys, time, urllib.request, urllib.parse, datetime
RUN = random.randint(1000, 9999)  # intake dedupe memory survives n8n restarts, so use fresh numbers each run

CFG = json.load(open(sys.argv[1]))
N8N = "http://127.0.0.1:5688/webhook/"
MOCK = "http://127.0.0.1:9099"
TOK = CFG["n8n"]["tokens"]
results = []


def http(url, data=None, form=False, method=None):
    if data is not None:
        body = urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
        req = urllib.request.Request(url, body, {"content-type": "application/x-www-form-urlencoded" if form else "application/json"}, method=method)
    else:
        req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            t = r.read().decode()
            return r.status, t
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def state():
    return json.loads(http(MOCK + "/__state")[1])


def reset(**flags):
    http(MOCK + "/__reset")
    if flags:
        http(MOCK + "/__flags", flags)


def seed(**kw):
    http(MOCK + "/__seed", kw)


def tool(name, args, phone="+15551230001", call_id="call_t1"):
    payload = {"message": {"type": "tool-calls", "call": {"id": call_id, "customer": {"number": phone}},
                           "toolCallList": [{"id": "tc1", "type": "function", "function": {"name": name, "arguments": args}}]}}
    code, t = http(N8N + "vapi-tool-" + name.replace("_", "-"), payload)
    try:
        res = json.loads(json.loads(t)["results"][0]["result"])
    except Exception:
        res = {"_raw": t[:300], "_code": code}
    return res


def intake(lead):
    return http(N8N + "lead-intake-" + TOK["intake"], lead)


def eoc(call_id, phone="+15551230001", reason="customer-ended-call", transcript="AI: Hi\nUser: Hello there", variables=None, summary="sum", duration=60):
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    msg = {"type": "end-of-call-report", "endedReason": reason, "summary": summary,
           "startedAt": (now - datetime.timedelta(seconds=duration)).isoformat() + "Z", "endedAt": now.isoformat() + "Z",
           "call": {"id": call_id, "type": "outboundPhoneCall", "customer": {"number": phone},
                    "assistantOverrides": {"variableValues": variables or {"firstName": "Sam", "propertyInterest": "2BR", "attempt": "1"}}},
           "analysis": {"summary": summary}, "artifact": {"transcript": transcript, "recordingUrl": "https://rec.example/" + call_id, "messages": []}}
    return http(N8N + "vapi-events", {"message": msg})


def sms_in(frm, body):
    return http(N8N + "twilio-sms-" + TOK["twilio"], {"From": frm, "To": CFG["twilio"].get("from_number", "+15550000000"), "Body": body, "MessageSid": "SMx"}, form=True)


def wait(pred, secs=25):
    t = time.time()
    while time.time() - t < secs:
        s = state()
        if pred(s):
            return s
        time.sleep(0.7)
    return state()


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  -> " + str(detail)[:400]))


def contact(s, phone):
    return next((c for c in s["contacts"].values() if c.get("phone") == phone), None)


def paths(s):
    return [(r["method"], r["path"]) for r in s["reqs"] if not r["path"].startswith("/__")]


def next_weekday_iso(hour_local_utc=14, days=2):
    d = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(days=days)
    return d.replace(hour=hour_local_utc, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- intake
def t_intake():
    reset()
    code, _ = intake({"firstName": "Sam", "phone": "+1555%d31" % RUN, "email": "s@x.com", "consent": True, "source": "web", "propertyInterest": "2BR"})
    s = wait(lambda s: len(s["calls"]) >= 1 and s["opps"] and len(s["reqs"]) > 8)
    check("intake: valid lead -> 200 and one Vapi call placed", code == 200 and len(s["calls"]) == 1, (code, len(s["calls"])))
    cb = s["calls"][0]["body"] if s["calls"] else {}
    check("intake: call targets E.164 number with outbound assistant", cb.get("customer", {}).get("number") == "+1555%d31" % RUN and "assistantId" in cb, cb)
    check("intake: variableValues carry firstName", cb.get("assistantOverrides", {}).get("variableValues", {}).get("firstName") == "Sam", cb.get("assistantOverrides"))
    c = contact(s, "+1555%d31" % RUN)
    check("intake: GHL contact tagged + opportunity created", c and "ai-lead" in c["tags"] and len(s["opps"]) == 1, (c, s["opps"]))

    # duplicate within dedupe window (same lead submitted twice, e.g. form + ad platform)
    reset()
    intake({"firstName": "Dup", "phone": "+1555%d03" % RUN, "consent": True})
    wait(lambda s: len(s["calls"]) >= 1)
    intake({"firstName": "Dup", "phone": "+1555%d03" % RUN, "consent": True})
    time.sleep(7)
    check("intake: same lead submitted twice -> only one call", len(state()["calls"]) == 1, len(state()["calls"]))

    # no consent
    reset()
    intake({"firstName": "NoC", "phone": "+15551230004"})
    time.sleep(6)
    s = state()
    check("intake: missing consent flag -> no call", len(s["calls"]) == 0, s["calls"])

    # invalid phone
    reset()
    code, t = intake({"firstName": "Bad", "phone": "12", "consent": True})
    time.sleep(4)
    check("intake: invalid phone -> no call", len(state()["calls"]) == 0, t)

    # DNC
    reset()
    seed(sheet="DNC", rows=[["+15551230005", "now", "test", ""]])
    intake({"firstName": "Dnc", "phone": "+15551230005", "consent": True})
    time.sleep(6)
    check("intake: DNC number -> no call", len(state()["calls"]) == 0, state()["calls"])

    # batch
    reset()
    intake({"leads": [{"firstName": "A", "phone": "+1555%d11" % RUN, "consent": True}, {"firstName": "B", "phone": "+1555%d12" % RUN, "consent": True}, {"firstName": "C", "phone": "+1555%d13" % RUN}]})
    s = wait(lambda s: len(s["calls"]) >= 2, 40)
    check("intake: batch of 3 (one without consent) -> 2 calls", len(s["calls"]) == 2, len(s["calls"]))


# ---------------------------------------------------------------- tools
def t_tools():
    reset()
    r = tool("check_availability", {"preferredDate": (datetime.date.today() + datetime.timedelta(days=2)).isoformat(), "timeOfDay": "any"})
    slots = r.get("slots") or r.get("options") or []
    check("check_availability: ok with slots", r.get("ok") and len(slots) > 0, r)
    start = next_weekday_iso()
    b = tool("book_viewing", {"startISO": start, "firstName": "Sam", "listing": "L-2001", "email": "s@x.com"})
    s = wait(lambda s: len(s["sms"]) >= 1 and len(s["events"]) >= 1, 25)
    check("book_viewing: ok", b.get("ok"), b)
    check("book_viewing: calendar event created", len(s["events"]) == 1, s["events"])
    check("book_viewing: confirmation SMS sent to lead", len(s["sms"]) == 1 and s["sms"][0]["to"] == "+15551230001", s["sms"])
    c = contact(s, "+15551230001")
    s = wait(lambda s: contact(s, "+15551230001") and len(s["opps"]) >= 1 and len(s["notes"]) >= 1)
    c = contact(s, "+15551230001")
    check("book_viewing: GHL contact + stage move", c is not None and len(s["opps"]) >= 1, (c, s["opps"]))
    b2 = tool("book_viewing", {"startISO": start, "firstName": "Other"}, phone="+15551230099", call_id="call_t2")
    check("book_viewing: double booking refused", b2.get("ok") is False, b2)
    s = state()
    check("book_viewing: no second event for refused booking", len(s["events"]) == 1, len(s["events"]))
    past = tool("book_viewing", {"startISO": "2020-01-01T10:00:00Z"})
    check("book_viewing: past time refused", past.get("ok") is False, past)

    reset()
    r = tool("search_listings", {"query": "pet friendly", "intent": "rent", "minBedrooms": 2})
    check("search_listings: filters apply", r.get("ok") and all(m["bedrooms"] >= 2 for m in r.get("matches", [])) and r.get("matches"), r)
    r = tool("search_listings", {"listingRef": "L-9999"})
    check("search_listings: unknown listing -> no matches, not an error", r.get("ok") and not r.get("matches"), r)

    reset(failCalendar=True)
    r = tool("check_availability", {})
    check("check_availability: calendar outage -> graceful failure message", r.get("ok") is False and r.get("message"), r)

    # qualification
    reset()
    r = tool("save_qualification", {"intent": "buy", "budgetMax": 600000, "preferredArea": "Riverside", "bedrooms": 3, "timeline": "within 1 month",
                                    "financing": "Pre-approved", "viewingInterest": True, "wantsAgent": True, "firstName": "Sam"})
    s = wait(lambda s: len(s["opps"]) >= 1 and len(s["sms"]) >= 1 and contact(s, "+15551230001") and len(contact(s, "+15551230001")["tags"]) >= 3, 30)
    check("save_qualification: hot lead", r.get("ok") and r.get("tier") == "hot", r)
    c = contact(s, "+15551230001")
    check("save_qualification: tier tag + hot-lead opportunity stage", c and any("hot" in t for t in c["tags"]) and len(s["opps"]) >= 1 and s["opps"][0].get("pipelineStageId") == "stage_qualified_hot", (c, s["opps"]))
    hot_alert = [x for x in s["sms"] if x["to"] != "+15551230001"]
    check("save_qualification: hot lead alert SMS goes to an agent", len(hot_alert) >= 1 or not CFG["agents"], s["sms"])
    reset()
    r = tool("save_qualification", {"intent": "rent", "timeline": "just browsing", "outcome": "not_interested"})
    check("save_qualification: not_interested forces cold", r.get("ok") and r.get("tier") == "cold", r)

    # callbacks
    reset()
    when = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%dT10:30:00")
    r = tool("schedule_callback", {"callbackLocalTime": when, "reason": "busy now", "firstName": "Sam"})
    s = wait(lambda s: len(s["sheets"]["Callbacks"]) >= 2)
    check("schedule_callback: row queued", r.get("ok") and len(s["sheets"]["Callbacks"]) == 2, r)

    # opt-out
    reset()
    seed(sheet="Callbacks", rows=[["cb1", "t", "2999-01-01T10:00:00Z", "", "+15551230001", "Sam", "x", "ai", 1, "pending", ""]])
    r = tool("opt_out", {"reason": "stop calling"})
    s = wait(lambda s: len(s["sheets"]["DNC"]) >= 2 and len(s["notes"]) >= 1 and len(s["sheets"]["Callbacks"][1]) > 9 and s["sheets"]["Callbacks"][1][9] == "cancelled", 40)
    check("opt_out: ok", r.get("ok"), r)
    check("opt_out: number added to DNC sheet", any(row and row[0] == "+15551230001" for row in s["sheets"]["DNC"][1:]), s["sheets"]["DNC"])
    c = contact(s, "+15551230001")
    check("opt_out: GHL dnd set", c and c.get("dnd") is True, c)
    cbrow = s["sheets"]["Callbacks"][1] if len(s["sheets"]["Callbacks"]) > 1 else []
    check("opt_out: pending callbacks cancelled", len(cbrow) > 9 and cbrow[9] == "cancelled", cbrow)


# ---------------------------------------------------------------- end of call
def t_eoc():
    reset()
    eoc("call_e1", reason="customer-did-not-answer", transcript="")
    s = wait(lambda s: len(s["sheets"]["CallLog"]) >= 2 and len(s["sheets"]["Callbacks"]) >= 2, 40)
    row = s["sheets"]["CallLog"][-1]
    check("eoc: no-answer logged", len(s["sheets"]["CallLog"]) == 2 and row[7] == "no_answer", row)
    cb = s["sheets"]["Callbacks"]
    check("eoc: no-answer schedules retry callback", len(cb) == 2, cb)

    reset()
    eoc("call_e2", transcript="AI: Hi\nUser: yes interested")
    s = wait(lambda s: len(s["notes"]) >= 1 and len(s["sheets"]["CallLog"]) >= 2)
    c = contact(s, "+15551230001")
    check("eoc: conversation -> note with summary on contact", len(s["notes"]) == 1 and c, (s["notes"], c))
    check("eoc: conversation logged", s["sheets"]["CallLog"][-1][7] == "conversation", s["sheets"]["CallLog"])
    eoc("call_e2", transcript="AI: Hi\nUser: yes interested")
    time.sleep(6)
    s = state()
    check("eoc: duplicate event ignored", len(s["sheets"]["CallLog"]) == 2 and len(s["notes"]) == 1, (len(s["sheets"]["CallLog"]), len(s["notes"])))

    reset()
    eoc("call_e3", transcript="AI: Hi\nUser: please stop calling me, remove me from your list")
    s = wait(lambda s: len(s["sheets"]["DNC"]) >= 2)
    c = contact(s, "+15551230001")
    check("eoc: transcript opt-out fallback adds DNC + dnd", len(s["sheets"]["DNC"]) == 2 and c and c.get("dnd") is True, (s["sheets"]["DNC"], c))

    reset()
    eoc("call_e4", reason="voicemail", transcript="AI: hello")
    s = wait(lambda s: len(s["sheets"]["CallLog"]) >= 2)
    check("eoc: voicemail outcome", s["sheets"]["CallLog"][-1][7] == "voicemail", s["sheets"]["CallLog"])

    # attempts exhausted
    reset()
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat() + "Z"
    seed(sheet="CallLog", rows=[[now, "p1", "outbound", "+15551230001", "", 5, "x", "no_answer", "", "", "", ""], [now, "p2", "outbound", "+15551230001", "", 5, "x", "no_answer", "", "", "", ""]])
    eoc("call_e5", reason="customer-did-not-answer", transcript="")
    s = wait(lambda s: len(s["sheets"]["CallLog"]) >= 4)
    check("eoc: third failed attempt -> no more callbacks queued", len(s["sheets"]["Callbacks"]) == 1, s["sheets"]["Callbacks"])

    # not an end-of-call message
    reset()
    code, _ = http(N8N + "vapi-events", {"message": {"type": "status-update", "status": "in-progress"}})
    time.sleep(2)
    check("eoc: non end-of-call events are acknowledged and ignored", code == 200 and len(state()["sheets"]["CallLog"]) == 1, code)


# ---------------------------------------------------------------- sms / scheduler / errors
def t_sms():
    reset()
    code, t = sms_in("+15551230001", "STOP")
    s = wait(lambda s: len(s["sheets"]["DNC"]) >= 2)
    check("sms: STOP -> TwiML 200 and DNC row", code == 200 and len(s["sheets"]["DNC"]) == 2, (code, t[:200]))
    c = contact(s, "+15551230001")
    check("sms: STOP sets GHL dnd", c and c.get("dnd") is True, c)
    reset()
    code, t = sms_in("+15551230001", "Can we move my viewing to Friday?")
    s = wait(lambda s: len(s["notes"]) >= 1)
    check("sms: normal reply is logged to GHL and not treated as opt-out", len(s["notes"]) >= 1 and len(s["sheets"]["DNC"]) == 1, (s["notes"], s["sheets"]["DNC"]))


def t_scheduler():
    reset()
    past = (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    seed(sheet="Callbacks", rows=[["cb_due", "t", past, "", "+15551230021", "Due", "retry", "ai", 1, "pending", ""],
                                  ["cb_future", "t", "2999-01-01T10:00:00Z", "", "+15551230022", "Fut", "retry", "ai", 1, "pending", ""],
                                  ["cb_dnc", "t", past, "", "+15551230023", "Dnc", "retry", "ai", 1, "pending", ""]])
    seed(sheet="DNC", rows=[["+15551230023", "t", "x", ""]])
    s = wait(lambda s: len(s["calls"]) >= 1, 100)
    check("scheduler: due callback is called", any(c["body"]["customer"]["number"] == "+15551230021" for c in s["calls"]), s["calls"])
    time.sleep(3)
    s = state()
    nums = [c["body"]["customer"]["number"] for c in s["calls"]]
    check("scheduler: future and DNC callbacks are not called", "+15551230022" not in nums and "+15551230023" not in nums, nums)
    st = {r[0]: r[9] if len(r) > 9 else "" for r in s["sheets"]["Callbacks"][1:]}
    check("scheduler: due row marked done, DNC row cancelled", st.get("cb_due") in ("done", "called") and st.get("cb_dnc") == "cancelled", st)


def t_errors():
    reset(failSms=True)
    start = next_weekday_iso(15, 3)
    b = tool("book_viewing", {"startISO": start, "firstName": "Sam"})
    s = wait(lambda s: any("SMS" in json.dumps(x["body"]) or "sms" in json.dumps(x["body"]).lower() for x in s["reqs"] if x["path"].endswith("Messages.json")) or len(s["events"]) >= 1, 25)
    time.sleep(6)
    s = state()
    check("error path: booking still succeeds when SMS provider fails", b.get("ok") and len(s["events"]) == 1, b)
    attempts = [r for r in s["reqs"] if r["path"].endswith("Messages.json")]
    check("error path: SMS failure triggers alert attempt (confirmation + admin alert both hit Twilio)", len(attempts) >= 2, len(attempts))


for fn in (t_intake, t_tools, t_eoc, t_sms, t_scheduler, t_errors):
    print("== " + fn.__name__)
    try:
        fn()
    except Exception as e:
        check(fn.__name__ + " crashed", False, repr(e))

bad = [r for r in results if not r[1]]
print("\n%d checks, %d failed" % (len(results), len(bad)))
sys.exit(1 if bad else 0)

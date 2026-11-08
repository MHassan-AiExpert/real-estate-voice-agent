"""Vapi tool, assistant and phone-number definitions, built from config.json + prompts/*.md."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TIMELINE = ["immediately", "within_1_month", "1_3_months", "3_6_months", "6_plus_months", "just_browsing", "unknown"]
FINANCING = ["cash", "pre_approved", "ready_to_apply", "mortgage_in_progress", "not_started", "not_ready", "unknown"]

S = lambda d, **kw: dict({"type": "string", "description": d}, **kw)
N = lambda d: {"type": "number", "description": d}
B = lambda d: {"type": "boolean", "description": d}

# name -> (webhook slug, description, parameters, required)
FUNCTION_TOOLS = {
    "check_availability": ("check-availability",
        "Look up open property-viewing slots in the agent calendar. Call this before offering any viewing time. Never invent times.",
        {"preferredDate": S("Day the lead prefers as YYYY-MM-DD in the agency's local time. Leave out if they have no preference."),
         "timeOfDay": S("Part of the day the lead prefers.", enum=["morning", "afternoon", "evening", "any"])}, []),
    "book_viewing": ("book-viewing",
        "Book a property viewing in the calendar and text the lead a confirmation. Only call after the lead has clearly agreed to a specific slot returned by check_availability. Use the exact startISO value from that tool.",
        {"startISO": S("Exact start time of the chosen slot, ISO 8601, copied from check_availability."),
         "firstName": S("Lead's first name."), "lastName": S("Lead's last name, if given."),
         "email": S("Lead's email address if they offered it."),
         "listing": S("Listing id or address the viewing is for."),
         "notes": S("Anything the agent should know before the viewing.")}, ["startISO"]),
    "search_listings": ("search-listings",
        "Search the agency's active listings. Use this before stating any fact about a property: price, bedrooms, availability, pets, parking. Never answer listing questions from memory.",
        {"query": S("Free-text description, e.g. 'pet friendly apartment near the station'."),
         "intent": S("buy or rent.", enum=["buy", "rent"]),
         "area": S("Neighbourhood or city."),
         "minBedrooms": N("Minimum number of bedrooms."),
         "maxPrice": N("Maximum price (sale price, or monthly rent)."),
         "minPrice": N("Minimum price."),
         "propertyType": S("e.g. apartment, house, condo, townhouse, studio."),
         "listingRef": S("A listing id or street address the caller mentioned.")}, []),
    "save_qualification": ("save-qualification",
        "Save what you learned about the lead so it is scored and written to the CRM. Call it once you have the main answers, or when the call is ending, even if some answers are missing. Returns the lead tier and whether a live transfer is available.",
        {"intent": S("Whether the lead wants to buy or rent.", enum=["buy", "rent"]),
         "budgetMin": N("Minimum budget as a plain number."), "budgetMax": N("Maximum budget as a plain number."),
         "preferredArea": S("Preferred neighbourhood or area."), "bedrooms": N("Number of bedrooms wanted."),
         "timeline": S("How soon they want to move.", enum=TIMELINE),
         "financing": S("How they will pay or finance.", enum=FINANCING),
         "viewingInterest": S("Whether they want to see a property.", enum=["yes", "maybe", "no"]),
         "wantsAgent": B("True if they asked to speak to a person."),
         "outcome": S("Set to not_interested only if the lead said they are not interested.", enum=["interested", "not_interested"]),
         "firstName": S("Lead's first name."), "lastName": S("Lead's last name."), "email": S("Email address."),
         "notes": S("One or two sentences of anything else useful for the agent.")}, ["intent"]),
    "schedule_callback": ("schedule-callback",
        "Schedule a follow-up call when the lead says it is a bad time or asks to be called back, or when an agent should call them. Pass the time in the agency's LOCAL time, never UTC.",
        {"callbackLocalTime": S("Local date and time, format YYYY-MM-DDTHH:MM:SS (no timezone suffix)."),
         "reason": S("Why: busy, not_ready, wants_agent_callback, language, other."),
         "notes": S("Context for whoever calls back."), "firstName": S("Lead's first name.")}, ["callbackLocalTime"]),
    "opt_out": ("opt-out",
        "Permanently stop all calls and texts to this number. Call this immediately when the lead asks to stop being contacted, then end the call.",
        {"reason": S("What the lead said, briefly.")}, []),
}


def fn_tool(name, base_url, secret):
    slug, desc, props, req = FUNCTION_TOOLS[name]
    params = {"type": "object", "properties": props}
    if req:
        params["required"] = req
    return {"type": "function", "async": False,
            "function": {"name": name, "description": desc, "parameters": params},
            "server": {"url": "%s/webhook/vapi-tool-%s" % (base_url.rstrip("/"), slug), "secret": secret, "timeoutSeconds": 20}}


def transfer_tool(cfg, say):
    nums = cfg["agents"].get("transfer_numbers") or []
    if not nums:
        return None
    return {"type": "transferCall",
            "destinations": [{"type": "number", "number": n, "numberE164CheckEnabled": True,
                              "message": say, "description": "Live transfer to a human agent"} for n in nums],
            "function": {"name": "transfer_call", "description": "Transfer the call to a human agent. Only after save_qualification returned transferAvailable true AND the lead agreed to be transferred."}}


def end_tool():
    return {"type": "endCall", "function": {"name": "end_call", "description": "End the call after saying goodbye, or immediately after opt_out."}}


def read_prompt(cfg, fname):
    t = open(os.path.join(ROOT, "prompts", fname), encoding="utf-8").read()
    a = cfg["agency"]
    for k, v in {"AGENCY_NAME": a["name"], "PERSONA_NAME": a["persona_name"], "TIMEZONE": a["timezone"],
                 "OFFICE_HOURS": a["office_hours_text"], "TRANSFER_NAME": a["transfer_name"], "AGENCY_PHONE": a["phone"]}.items():
        t = t.replace("[[%s]]" % k, v)
    assert "[[" not in t, "unreplaced token in " + fname
    return t


def first_message(cfg, inbound):
    a, v = cfg["agency"], cfg["vapi"]
    notice = v.get("recording_notice", "").strip()
    if inbound:
        return "Thanks for calling %s, I am %s, the virtual assistant. This call may be recorded for quality. How can I help you today?" % (a["name"], a["persona_name"])
    return ("Hi {{firstName}}, this is %s, the virtual assistant from %s. You recently asked about {{propertyInterest}}. %s Do you have a quick minute?"
            % (a["persona_name"], a["name"], notice)).replace("  ", " ")


def assistant(cfg, inbound, tool_ids, base_url, secret):
    v = cfg["vapi"]
    a = {
        "name": "%s - %s" % (cfg["agency"]["name"], "Inbound" if inbound else "Outbound"),
        "model": {"provider": v["llm_provider"], "model": v["llm_model"], "temperature": 0.2,
                  "messages": [{"role": "system", "content": read_prompt(cfg, "inbound-system-prompt.md" if inbound else "outbound-system-prompt.md")}],
                  "toolIds": tool_ids},
        "voice": v["voice"], "transcriber": v["transcriber"],
        "firstMessage": first_message(cfg, inbound),
        "firstMessageMode": "assistant-speaks-first",
        "maxDurationSeconds": v["max_call_seconds"],
        "silenceTimeoutSeconds": 45,
        "endCallFunctionEnabled": bool(tool_ids),
        "recordingEnabled": True,
        "backgroundDenoisingEnabled": True,
        "server": {"url": "%s/webhook/vapi-events" % base_url.rstrip("/"), "secret": secret, "timeoutSeconds": 20},
        "serverMessages": ["end-of-call-report"],
        "analysisPlan": {"summaryPlan": {"enabled": True, "messages": [
            {"role": "system", "content": "Summarise this real-estate call in 3 short sentences for a sales agent: what the lead wants (buy/rent, budget, area, bedrooms), their timeline and financing, and the outcome (viewing booked, callback, not interested, opted out)."},
            {"role": "user", "content": "Transcript:\n\n{{transcript}}"}]}},
    }
    if not inbound:
        a["voicemailDetection"] = {"provider": "vapi"}
        a["voicemailMessage"] = v.get("voicemail_message") or (
            "Hi {{firstName}}, this is %s from %s about your property enquiry. I'll try you again soon, or call us back on %s. Thanks!"
            % (cfg["agency"]["persona_name"], cfg["agency"]["name"], cfg["agency"]["phone"]))
    return a

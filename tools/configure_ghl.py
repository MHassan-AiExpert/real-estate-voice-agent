#!/usr/bin/env python3
"""Look up GoHighLevel ids and write them into config.json.

  GHL_TOKEN=<private integration token> python3 tools/configure_ghl.py discover   # print pipelines / stages / custom fields
  GHL_TOKEN=... python3 tools/configure_ghl.py setup                              # create missing ai_* custom fields, map pipeline + stage ids

Create the pipeline by hand first (names in config.json -> ghl.pipeline_name and ghl.stage_names); the API can read pipelines, not build them.
Custom-field creation uses POST /locations/{id}/customFields; if your account rejects it, create the fields by hand
(Settings -> Custom Fields, object: Contact, type: text) with the names printed by `discover`, then re-run `setup` to map their ids.
"""
import json, os, sys, urllib.request, urllib.error, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
FIELDS = {  # key -> (GHL field name, data type)
    "ai_intent": "AI Intent", "ai_budget_min": "AI Budget Min", "ai_budget_max": "AI Budget Max", "ai_preferred_area": "AI Preferred Area",
    "ai_bedrooms": "AI Bedrooms", "ai_timeline": "AI Timeline", "ai_financing": "AI Financing", "ai_lead_score": "AI Lead Score",
    "ai_lead_tier": "AI Lead Tier", "ai_lead_source": "AI Lead Source", "ai_call_summary": "AI Call Summary", "ai_notes": "AI Notes",
    "ai_last_call_id": "AI Last Call Id", "ai_last_call_at": "AI Last Call At", "ai_call_attempts": "AI Call Attempts",
    "ai_next_callback_at": "AI Next Callback At", "ai_opt_out": "AI Opt Out", "ai_viewing_datetime": "AI Viewing Datetime",
    "ai_viewing_event_id": "AI Viewing Event Id", "ai_viewing_call_id": "AI Viewing Call Id"}


def call(base, token, method, path, body=None):
    req = urllib.request.Request(base.rstrip("/") + path, json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Authorization": "Bearer " + token, "Version": "2021-07-28", "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "voice-agent-setup/1.0 (+python)"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:500]}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "discover"
    token = os.environ.get("GHL_TOKEN") or sys.exit("Set GHL_TOKEN (Private Integration token).")
    path = os.path.join(ROOT, "config.json")
    cfg = json.load(open(path)); g = cfg["ghl"]; base = cfg["api_base"]["ghl"]
    loc = g.get("location_id") or sys.exit("Set ghl.location_id in config.json first.")

    code, fields = call(base, token, "GET", "/locations/%s/customFields?model=contact" % loc)
    if code != 200:
        sys.exit("Could not read custom fields (%s): %s" % (code, fields))
    by_name = {f["name"].lower(): f["id"] for f in fields.get("customFields", [])}
    code, pl = call(base, token, "GET", "/opportunities/pipelines?locationId=" + urllib.parse.quote(loc))
    if code != 200:
        sys.exit("Could not read pipelines (%s): %s" % (code, pl))
    pipe = next((p for p in pl.get("pipelines", []) if p["name"].lower() == g["pipeline_name"].lower()), None)

    if mode == "discover":
        print("Pipelines:")
        for p in pl.get("pipelines", []):
            print("  %s  %s" % (p["id"], p["name"]), [s["name"] for s in p.get("stages", [])])
        print("Custom fields found:", sorted(by_name))
        print("Missing AI fields:", [v for v in FIELDS.values() if v.lower() not in by_name])
        return

    for key, name in FIELDS.items():
        if name.lower() in by_name:
            continue
        code, r = call(base, token, "POST", "/locations/%s/customFields" % loc, {"name": name, "dataType": "TEXT", "model": "contact"})
        fid = (r.get("customField") or r).get("id") if code in (200, 201) else None
        if fid:
            by_name[name.lower()] = fid; print("created field", name)
        else:
            print("COULD NOT CREATE '%s' (%s %s). Create it by hand (Contact, text) and re-run setup." % (name, code, r))
    g["custom_fields"] = {k: by_name[n.lower()] for k, n in FIELDS.items() if n.lower() in by_name}
    if not pipe:
        print("Pipeline '%s' not found. Create it with these stages: %s" % (g["pipeline_name"], list(g["stage_names"].values())))
    else:
        g["pipeline_id"] = pipe["id"]
        sid = {s["name"].lower(): s["id"] for s in pipe.get("stages", [])}
        g["stage_ids"] = {k: sid[v.lower()] for k, v in g["stage_names"].items() if v.lower() in sid}
        miss = [v for k, v in g["stage_names"].items() if k not in g["stage_ids"]]
        if miss:
            print("Stages missing in the pipeline (add them): ", miss)
    json.dump(cfg, open(path, "w"), indent=2); open(path, "a").write("\n")
    print("Saved to config.json: %d custom fields, pipeline %s, %d stages." % (len(g["custom_fields"]), g.get("pipeline_id") or "-", len(g.get("stage_ids", {}))))


if __name__ == "__main__":
    main()

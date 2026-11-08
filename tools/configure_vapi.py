#!/usr/bin/env python3
"""Create or update the Vapi tools, the inbound + outbound assistants and the Twilio phone number.

  VAPI_API_KEY=... TWILIO_AUTH_TOKEN=... python3 tools/configure_vapi.py            # create/update everything
  python3 tools/configure_vapi.py --dry-run                                         # write vapi/*.json only, no API calls

Idempotent: ids are kept in vapi/.state.json and written back to config.json, so re-running updates in place.
After running it, re-run tools/build_workflows.py (it embeds the assistant / phone-number ids) and re-import workflow 01/03/05.
"""
import argparse, json, os, sys, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import vapi_defs as D  # noqa: E402


def api(key, base, method, path, body=None):
    req = urllib.request.Request(base.rstrip("/") + path, json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "voice-agent-setup/1.0 (+python)"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        sys.exit("Vapi %s %s failed (%s): %s" % (method, path, e.code, e.read().decode()[:800]))


def upsert(key, base, kind, existing_id, body):
    """POST creates; PATCH updates. Vapi rejects some create-only fields on PATCH, so drop 'type' for tools."""
    if existing_id:
        b = dict(body)
        b.pop("type", None)
        return api(key, base, "PATCH", "/%s/%s" % (kind, existing_id), b)["id"]
    return api(key, base, "POST", "/" + kind, body)["id"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(ROOT, "config.json"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cfg = json.load(open(args.config))
    base_url = cfg["n8n"]["base_url"]
    secret = cfg["n8n"]["tokens"].get("vapi_secret") or sys.exit("Run tools/build_workflows.py first (it generates n8n.tokens.vapi_secret).")
    base = cfg["api_base"]["vapi"]
    out = os.path.join(ROOT, "vapi"); os.makedirs(out, exist_ok=True)
    state_path = os.path.join(out, ".state.json")
    state = json.load(open(state_path)) if os.path.exists(state_path) else {}
    key = os.environ.get("VAPI_API_KEY", "")
    live = not args.dry_run
    if live and not key:
        sys.exit("Set VAPI_API_KEY (Vapi dashboard -> API Keys -> private key), or use --dry-run.")

    say = "One moment, I'll connect you to %s now." % cfg["agency"]["transfer_name"]
    defs = {n: D.fn_tool(n, base_url, secret) for n in D.FUNCTION_TOOLS}
    t = D.transfer_tool(cfg, say)
    if t:
        defs["transfer_call"] = t
    defs["end_call"] = D.end_tool()

    ids = dict(state.get("tools", {}))
    for name, body in defs.items():
        json.dump(body, open(os.path.join(out, "tool-%s.json" % name), "w"), indent=2)
        if live:
            ids[name] = upsert(key, base, "tool", ids.get(name), body)
            print("tool %-20s %s" % (name, ids[name]))
    tool_ids = [ids[n] for n in defs if n in ids] if live else ["<%s>" % n for n in defs]
    inbound_tools = tool_ids  # same toolbox; the prompts decide when each is used
    _demo = 'DEMO-MODE-START' in open(os.path.join(ROOT, 'prompts', 'inbound-system-prompt.md'), encoding='utf-8').read()
    if _demo and live:
        tool_ids = inbound_tools = []
        print('DEMO MODE: no tools attached (n8n tools and end_call off)')

    a_in = D.assistant(cfg, True, inbound_tools, base_url, secret)
    a_out = D.assistant(cfg, False, tool_ids, base_url, secret)
    json.dump(a_in, open(os.path.join(out, "assistant-inbound.json"), "w"), indent=2)
    json.dump(a_out, open(os.path.join(out, "assistant-outbound.json"), "w"), indent=2)
    if not live:
        print("dry run: wrote vapi/*.json (%d tools, 2 assistants)" % len(defs)); return

    v = cfg["vapi"]
    aid_in = upsert(key, base, "assistant", v.get("inbound_assistant_id") or state.get("inbound"), a_in)
    aid_out = upsert(key, base, "assistant", v.get("outbound_assistant_id") or state.get("outbound"), a_out)
    print("assistant inbound  ", aid_in); print("assistant outbound ", aid_out)

    # verify what Vapi actually holds now
    chk = api(key, base, "GET", "/assistant/" + aid_in)
    msg = (chk.get("model", {}).get("messages") or [{}])[0].get("content", "")
    print("\nVERIFY inbound assistant as stored in Vapi:")
    print("  first message :", chk.get("firstMessage"))
    print("  temperature   :", chk.get("model", {}).get("temperature"))
    print("  tools attached:", len(chk.get("model", {}).get("toolIds") or []))
    print("  silence timeout:", chk.get("silenceTimeoutSeconds"), "| max seconds:", chk.get("maxDurationSeconds"))
    print("  prompt has OVERRIDE rule:", "OVERRIDE (highest priority)" in msg, "| has CALL HANDLING rules:", "DEMO CALL HANDLING" in msg)
    print("  version/published info:", {k: chk[k] for k in chk if "version" in k.lower() or "publish" in k.lower() or k in ("updatedAt",)})

    pn_id = v.get("phone_number_id") or state.get("phone_number_id")
    tw_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not pn_id:
        if not tw_token:
            print("SKIPPED phone number import: set TWILIO_AUTH_TOKEN to import %s into Vapi." % cfg["twilio"]["from_number"])
        else:
            pn_id = api(key, base, "POST", "/phone-number", {
                "provider": "twilio", "number": cfg["twilio"]["from_number"], "twilioAccountSid": cfg["twilio"]["account_sid"],
                "twilioAuthToken": tw_token, "name": cfg["agency"]["name"] + " main line", "assistantId": aid_in,
                "server": {"url": "%s/webhook/vapi-events" % base_url.rstrip("/"), "secret": secret}})["id"]
            print("phone number", pn_id)
    elif live:
        api(key, base, "PATCH", "/phone-number/" + pn_id, {"assistantId": aid_in})  # inbound calls -> inbound assistant

    state.update({"tools": ids, "inbound": aid_in, "outbound": aid_out, "phone_number_id": pn_id or ""})
    json.dump(state, open(state_path, "w"), indent=2)
    v.update({"inbound_assistant_id": aid_in, "outbound_assistant_id": aid_out, "phone_number_id": pn_id or "", "tool_ids": ids})
    json.dump(cfg, open(args.config, "w"), indent=2); open(args.config, "a").write("\n")
    print("\nDone. Now: python3 tools/build_workflows.py && re-import workflows 01, 03, 05 (they embed these ids).")


if __name__ == "__main__":
    main()

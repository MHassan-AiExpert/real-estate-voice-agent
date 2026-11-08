#!/usr/bin/env python3
"""Generate the n8n workflow JSON files from config.json.

  python3 tools/build_workflows.py                 # normal build -> workflows/*.json
  python3 tools/build_workflows.py --test --mock http://127.0.0.1:9099 --out /tmp/wf-test
      # test build: no credentials, unsecured webhooks, every external API pointed at a local mock server
"""
import argparse
import json
import os
import secrets
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from n8nkit import CRED  # noqa: E402
from wf_tools import build_tools_a, build_tools_b  # noqa: E402
from wf_flows import build_intake, build_eoc, build_sms, build_scheduler, build_error  # noqa: E402


def load_config(path):
    if not os.path.exists(path):
        shutil.copy(os.path.join(ROOT, "config.example.json"), path)
        print("Created %s from config.example.json - edit it and re-run." % path)
    with open(path) as f:
        return json.load(f)


def ensure_tokens(cfg, path, write):
    tokens = cfg.setdefault("n8n", {}).setdefault("tokens", {})
    changed = False
    for k, n in (("intake", 12), ("twilio", 12)):
        if not tokens.get(k):
            tokens[k] = secrets.token_hex(n)
            changed = True
    if not tokens.get("vapi_secret"):
        tokens["vapi_secret"] = secrets.token_urlsafe(24)
        changed = True
    if changed and write:
        with open(path, "w") as f:
            json.dump(cfg, f, indent=2)
            f.write("\n")
    return tokens


def runtime_config(cfg):
    """The subset of config.json that is embedded in the workflows (no secrets, no Vapi prompt settings)."""
    keep = ["agency", "calling", "viewing", "scoring", "agents", "admin", "twilio", "ghl", "google"]
    out = {k: cfg[k] for k in keep}
    out["vapi"] = {k: cfg["vapi"][k] for k in ("phone_number_id", "outbound_assistant_id", "inbound_assistant_id")}
    return out


def credentials_template(cfg, tokens):
    t = lambda k: CRED[k]
    return [
        {"id": t("ghl")[2], "name": t("ghl")[1], "type": t("ghl")[0], "data": {"token": "PASTE_GHL_PRIVATE_INTEGRATION_TOKEN"}},
        {"id": t("vapi")[2], "name": t("vapi")[1], "type": t("vapi")[0],
         "data": {"json": json.dumps({"headers": {"Authorization": "Bearer PASTE_VAPI_PRIVATE_API_KEY"}})}},
        {"id": t("twilio")[2], "name": t("twilio")[1], "type": t("twilio")[0],
         "data": {"user": cfg["twilio"]["account_sid"], "password": "PASTE_TWILIO_AUTH_TOKEN"}},
        {"id": t("webhook")[2], "name": t("webhook")[1], "type": t("webhook")[0],
         "data": {"name": "X-Vapi-Secret", "value": tokens["vapi_secret"]}},
        {"id": t("gcal")[2], "name": t("gcal")[1], "type": t("gcal")[0],
         "data": {"clientId": "PASTE_GOOGLE_CLIENT_ID", "clientSecret": "PASTE_GOOGLE_CLIENT_SECRET"}},
        {"id": t("gsheets")[2], "name": t("gsheets")[1], "type": t("gsheets")[0],
         "data": {"clientId": "PASTE_GOOGLE_CLIENT_ID", "clientSecret": "PASTE_GOOGLE_CLIENT_SECRET"}},
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(ROOT, "config.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "workflows"))
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--mock", default="")
    ap.add_argument("--ids", action="store_true",
                    help="give every workflow a fixed id and wire 06 Error Handler as error workflow of 01-05 "
                         "(use with `n8n import:workflow`; implied by --test)")
    ap.add_argument("--active", action="store_true", help="mark workflows active in the JSON (use with import --activeState fromJson)")
    args = ap.parse_args()
    use_ids = args.ids or args.test

    cfg = load_config(args.config)
    tokens = ensure_tokens(cfg, args.config, write=not args.test)
    A = dict(cfg["api_base"])
    if args.mock:
        A = {k: args.mock.rstrip("/") + "/" + k for k in A}
    rc = runtime_config(cfg)
    rc["api_base"] = A

    os.makedirs(args.out, exist_ok=True)
    flows = [
        ("01-lead-intake-outbound-call.json", build_intake(rc, A, args.test, tokens)),
        ("02a-vapi-tools-calendar-listings.json", build_tools_a(rc, A, args.test)),
        ("02b-vapi-tools-leads-compliance.json", build_tools_b(rc, A, args.test)),
        ("03-vapi-end-of-call-report.json", build_eoc(rc, A, args.test)),
        ("04-twilio-inbound-sms-optout.json", build_sms(rc, A, args.test, tokens)),
        ("05-callback-scheduler.json", build_scheduler(rc, A, args.test)),
        ("06-error-handler.json", build_error(rc, A, args.test)),
    ]
    wf_ids = {fname: "va%02d%s" % (i + 1, fname.split("-")[0].replace("a", "A").replace("b", "B") + "VoiceAgentWf")
              for i, (fname, _) in enumerate(flows)}
    err_id = wf_ids["06-error-handler.json"]
    for fname, wf in flows:
        d = wf.export()
        if use_ids:
            d["id"] = wf_ids[fname]
            if fname != "06-error-handler.json":
                d["settings"]["errorWorkflow"] = err_id
        if args.active or args.test:
            d["active"] = True
        with open(os.path.join(args.out, fname), "w") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print("wrote %-42s %3d nodes" % (fname, len(wf.nodes)))

    if not args.test:
        with open(os.path.join(args.out, "credentials.template.json"), "w") as f:
            json.dump(credentials_template(cfg, tokens), f, indent=2)
            f.write("\n")
        base = cfg["n8n"]["base_url"].rstrip("/")
        eps = [
            "# Webhook endpoints (production URLs)",
            "",
            "Generated by tools/build_workflows.py. Workflows must be ACTIVE for production URLs to respond.",
            "",
            "- Lead intake (forms / GHL webhook / lead lists): `%s/webhook/lead-intake-%s`" % (base, tokens["intake"]),
            "- Twilio inbound SMS webhook (set on the Twilio number, HTTP POST): `%s/webhook/twilio-sms-%s`" % (base, tokens["twilio"]),
            "- Vapi end-of-call events (assistant server URL): `%s/webhook/vapi-events`" % base,
            "- Vapi tools (set automatically by tools/configure_vapi.py):",
        ]
        for t in ["check-availability", "book-viewing", "search-listings", "save-qualification", "schedule-callback", "opt-out"]:
            eps.append("  - `%s/webhook/vapi-tool-%s`" % (base, t))
        eps += ["", "Vapi webhook header: `X-Vapi-Secret: <n8n.tokens.vapi_secret from config.json>`", ""]
        with open(os.path.join(args.out, "ENDPOINTS.md"), "w") as f:
            f.write("\n".join(eps))
        print("wrote credentials.template.json and ENDPOINTS.md")


if __name__ == "__main__":
    main()

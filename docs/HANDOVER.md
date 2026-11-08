# Handover guide

## What you have
An outbound agent that phones new leads within about a minute, qualifies them, books viewings and updates GoHighLevel; an inbound agent that answers listing questions from your Listings sheet; seven n8n workflows tying Vapi, Twilio, Google and GoHighLevel together; and opt-out/DNC handling.

## Daily and weekly care (about 10 minutes)
- **Listings tab:** mark sold/let listings `inactive` the same day. The inbound agent only knows what is in this tab.
- **Calendar:** keep agent availability on the connected Google Calendar; busy events block slots.
- **Alerts:** make sure `agents.alert_sms_numbers` reach someone who reads them. An alert means something failed (SMS, calendar, CRM or a tool).
- **n8n Executions page:** filter on *Error* weekly.
- **Callbacks tab:** rows with `handler = human` are for your agents to ring; `status` shows pending/done/cancelled.
- **CallLog tab:** one row per call with outcome, tier and summary; use it for reporting.
- **Listen to 5 recordings a week** in Vapi for the first month and tune `prompts/*.md`.

## Common fixes
| Symptom | Check |
|---------|-------|
| Lead did not get called | CallLog for `skipped:<reason>` (no consent, DNC, duplicate, bad number, outside window) |
| Calls not starting | Workflow 01 active? Vapi credit? `vapi.phone_number_id` set? |
| Viewing not booked | Google credential signed in? Alert SMS received? |
| Agent invents or misquotes a listing | Listings tab row wrong/missing; tighten the prompt |
| GHL fields empty | `ghl.custom_fields` ids missing: run `configure_ghl.py setup` and rebuild |
| STOP reply ignored | Twilio messaging webhook points to the `twilio-sms-` URL |

## Keys and secrets
GoHighLevel token, Vapi key, Twilio Auth Token and Google OAuth live only in n8n credentials. `config.json` holds webhook path tokens and the Vapi webhook secret: keep it private and do not commit it. Rotate by deleting the token in `config.json`, re-running `build_workflows.py` and `configure_vapi.py`, and re-importing.

## Walkthrough video outline (about 8 minutes)
1. (0:30) The problem and the flow: lead in -> call -> CRM -> viewing -> SMS.
2. (1:00) Config file and the three scripts to run.
3. (1:30) n8n: the seven workflows; open 01 and 02b and explain the gate, scoring and error handling.
4. (1:00) Vapi dashboard: the two assistants, the tools, the prompt.
5. (2:00) Live demo: submit a lead, take the call, book a viewing, show calendar, SMS, GoHighLevel record and agent alert.
6. (0:45) Say "stop calling" on a second call and show the DNC row and GHL DND.
7. (0:45) Inbound call about a listing.
8. (0:30) Where to look when something breaks, and who to contact.

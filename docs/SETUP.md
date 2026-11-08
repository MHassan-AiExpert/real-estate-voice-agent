# Setup guide

Time needed: about 90 minutes if the accounts already exist. Do it in this order.

## 0. Accounts and what you need
- **n8n** (cloud or self-hosted, v1.100+ / v2) with a public HTTPS URL.
- **Vapi** account and a **private API key**.
- **Twilio** account, a voice+SMS capable number, Account SID and Auth Token. Register the number for A2P 10DLC (US) before sending SMS at volume.
- **GoHighLevel** sub-account. Create a **Private Integration token** (Settings -> Private Integrations) with scopes: contacts (read/write), opportunities (read/write), locations/custom fields (read/write), locations/tags (read/write).
- **Google** account that owns the agent calendar and a Google Cloud OAuth client (Calendar API and Sheets API enabled; redirect URI = your n8n OAuth callback).
- Python 3.9+ on the machine where you run the setup scripts (no extra packages needed).

## 1. Configure
1. `cp config.example.json config.json` and fill in: agency name, persona, timezone, office hours text, `twilio.account_sid` and `from_number`, `agents.transfer_numbers` and `alert_sms_numbers`, `ghl.location_id`, `n8n.base_url`, `google.spreadsheet_id`, `google.calendar_id`.
2. Review `calling.window` (default Mon-Sat 09:00-20:00 local). **Set this to what is lawful where your leads live; see COMPLIANCE.md.**
3. `viewing.*` controls bookable hours, slot length, buffers and minimum notice.

## 2. GoHighLevel
1. Create a pipeline named as in `ghl.pipeline_name` with the stages listed in `ghl.stage_names` (New Lead, Contacted - No Answer, Qualified - Hot, Qualified - Warm, Nurture - Cold, Viewing Booked, Do Not Contact).
2. `GHL_TOKEN=... python3 tools/configure_ghl.py discover` then `... setup`. This creates the 20 `AI ...` custom fields (or tells you which to create by hand) and writes all ids into `config.json`.

## 3. Google Sheet
Create one spreadsheet with four tabs and these header rows (row 1, exactly):

- **Listings**: `listing_id, status, type, title, address, neighborhood, city, price, currency, bedrooms, bathrooms, sqft, property_type, furnished, pets_allowed, parking, available_from, highlights, viewing_notes, url` (import `knowledge-base/listings-sample.csv`, then replace with your real listings; only `status = active` rows are used)
- **Callbacks**: `callback_id, created_at, due_at, contact_id, phone, first_name, reason, handler, attempt, status, note`
- **DNC**: `phone, added_at, source, note`
- **CallLog**: `timestamp, call_id, direction, phone, contact_id, duration_s, ended_reason, outcome, tier, score, summary, recording_url`

Put the spreadsheet id in `config.json`. Keep listings current: the inbound agent can only be as accurate as this tab.

## 4. n8n
1. `python3 tools/build_workflows.py` generates `workflows/*.json`, `credentials.template.json` and `ENDPOINTS.md`, and writes secret tokens into `config.json`.
2. Edit `workflows/credentials.template.json` (paste the GoHighLevel token, Vapi key, Twilio Auth Token, Google client id/secret) then `n8n import:credentials --input=workflows/credentials.template.json`. Or create the six credentials in the UI with the exact names shown in the template.
3. In n8n open **Google Calendar** and **Google Sheets** credentials and click **Sign in with Google**.
4. `n8n import:workflow --separate --input=workflows`, or import each file in the UI.
5. Open **06 Error Handler**, then in workflows 01-05 -> Settings -> Error workflow = *06 Error Handler* (already set if you imported with the CLI).
6. **Activate** all seven workflows. Webhook URLs are in `workflows/ENDPOINTS.md`.

## 5. Vapi
1. `VAPI_API_KEY=... TWILIO_AUTH_TOKEN=... python3 tools/configure_vapi.py`. It creates the tools, both assistants, imports the Twilio number and routes inbound calls to the inbound assistant. Ids are saved to `config.json`.
2. Re-run `python3 tools/build_workflows.py` (it embeds the new assistant and phone ids) and re-import workflows 01, 03 and 05.
3. In Twilio set the number's **messaging webhook** (HTTP POST) to the `twilio-sms-...` URL from ENDPOINTS.md so STOP replies reach workflow 04. Voice is handled by Vapi.

## 6. Connect your lead sources
Send each source to `POST <n8n>/webhook/lead-intake-<token>`.
- Single lead: `{"firstName","lastName","phone","email","consent":true,"source","propertyInterest"}`
- Batch / lead list: `{"leads":[{...},{...}]}` (calls are spaced by `calling.call_interval_ms`).
- GoHighLevel: add a Workflow with the trigger *Contact Created* / *Form Submitted* and a *Webhook* action posting those fields.
- Facebook/Google lead ads and web forms: use their webhook, Zapier or Make, or forward through GHL.
`consent` must be true. With `calling.require_consent_flag` on (recommended), leads without it are skipped and logged.

## 7. Go-live checklist
Run TEST-PLAN.md end to end. Then: calling window and consent wording approved, recording notice approved, DNC sheet seeded with any existing opt-outs, error alerts reach a real person, one real test call from each of inbound and outbound.

# Local end-to-end test

Runs the real generated workflows in a throwaway n8n against mock GoHighLevel, Vapi, Twilio, Google Calendar and Google Sheets servers, then drives every webhook with realistic payloads.

```
npm install n8n@2.35.7 --prefix <scratch>/n8n-e2e         # once
cp config.json <scratch>/e2e/config.test.json             # then set: always-open calling/viewing/transfer windows, ghl.pipeline_id, ghl.stage_ids, google.spreadsheet_id=SHEET1, calling.scheduler_interval_minutes=1
LISTINGS_CSV=knowledge-base/listings-sample.csv node tests/e2e/mock_server.mjs &
tests/e2e/run_n8n.sh <scratch>                            # build (--test --mock), import, publish, start n8n on 127.0.0.1:5688
python3 tests/e2e/run_scenarios.py <scratch>/e2e/config.test.json
```
Expected: `46 checks, 0 failed`. It proves workflow logic and API payload shapes as the mock defines them; it does not prove the real GoHighLevel, Vapi, Twilio or Google APIs accept those payloads.

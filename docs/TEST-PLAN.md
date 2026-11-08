# Test plan (run before launch)

## A. Automated (no live accounts needed)
`tests/validate_workflows.py` (graph, references, JavaScript syntax) and `tests/e2e/` (local n8n + mock GoHighLevel/Vapi/Twilio/Calendar/Sheets). See `tests/e2e/README.md`. Expected: `RESULT: OK` and `46 checks, 0 failed`. This proves the workflow logic, not the third-party APIs.

## B. Live smoke tests (your accounts), in this order
1. **Config:** `configure_ghl.py discover` lists pipeline, stages and fields; `configure_vapi.py` finishes without errors.
2. **Tools one by one** (Vapi dashboard -> Tools -> test, or curl with header `X-Vapi-Secret`): search_listings, check_availability, book_viewing (a real calendar event + SMS to your own phone), save_qualification, schedule_callback, opt_out.
3. **Intake:** post each lead in `samples/sample-leads.json`, using YOUR phone numbers, and compare with the *Expect* text:
   - Alex: hot, transfer offered, viewing booked, SMS received, agent alert SMS, GHL fields + stage *Qualified - Hot* / *Viewing Booked*, summary and transcript in the note.
   - Priya: warm. Sam: cold. Jordan: callback appears in Callbacks sheet and fires at the time.
   - Chris: "stop calling" -> DNC row, DND on in GHL; re-submit the lead and confirm NO call.
   - Taylor: no consent -> no call. Robin: let it ring out -> voicemail, retry queued.
4. **Inbound:** call the Twilio number and ask about price, bedrooms, pets and parking for 3 listings; ask something not in the sheet (it must say an agent will confirm); ask for a person (transfer or callback depending on hours); book a viewing.
5. **SMS:** reply STOP to the confirmation, confirm DNC + DND.
6. **Failure drills:** turn off the Google credential and check you get an alert SMS and that the agent tells the caller an agent will follow up instead of inventing a time.
7. **Timing:** time from form submit to phone ringing (target under 60 seconds).

## C. Things to confirm in the first live calls (not provable offline)
- The `{{"now" | date: ...}}` line in the prompt renders the correct local date and time.
- Vapi echoes `variableValues` in the end-of-call report (workflow 03 falls back to the phone number if not).
- Transfer: warm transfer rings an agent phone and the lead hears the hand-off line.
- Voicemail detection correctly ends calls on machines.

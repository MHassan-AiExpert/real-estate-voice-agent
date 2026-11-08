# Calling consent, opt-out and recording: how the system behaves

**This is a description of the controls built in, not legal advice.** Rules differ by country, state and campaign type (for example the US TCPA and state mini-TCPA laws, FCC rules on AI-generated voices, CAN-SPAM/CASL equivalents, UK PECR, GDPR, and two-party-consent recording laws). Have your lawyer approve the consent wording, calling hours and recording notice for the places your leads live before launch. The system does not know where a lead lives, so it applies one configured calling window to everyone; set it to the strictest zone you serve.

## Controls in the system
- **Consent gate.** With `calling.require_consent_flag` on (default), a lead is only called if the incoming data has `consent: true`. Your form or ad must capture express consent to be called (and texted) by phone, including by an automated or AI assistant, and store the wording and timestamp. Skipped leads are written to the CallLog with the reason.
- **AI disclosure.** The first sentence says the caller is a virtual assistant. The prompts forbid claiming to be human.
- **Recording notice.** The opening line includes `vapi.recording_notice`. Change or remove it per your legal advice; for two-party-consent regions keep an explicit notice (or turn recording off in `tools/vapi_defs.py`).
- **Calling window.** `calling.window` (days and local hours) is enforced at intake and by the callback scheduler. Leads that arrive outside it are deferred to the next window start rather than dropped.
- **Do-not-call list.** The DNC sheet is checked before every outbound call (intake and scheduler). If the sheet cannot be read, the call is deferred, never placed.
- **Stop means stop.** On voice ("stop calling me", "remove me", "wrong number"), by SMS reply (STOP, UNSUBSCRIBE, "don't text me", etc.), or when the end-of-call transcript contains a clear stop request the agent missed: the number is added to DNC, GoHighLevel Do-Not-Disturb is set, pending callbacks are cancelled and the opportunity moves to *Do Not Contact*.
- **"Not now" is handled politely.** The assistant offers one callback; if declined it ends the call without pressure.
- **Attempt limits.** At most `calling.max_attempts` (default 3) with the delays in `retry_delays_minutes`.
- **Texts.** The only texts to leads are the viewing confirmation (includes "Reply STOP to opt out"). Twilio also enforces carrier-level STOP.
- **Voicemail.** A short message with no property or financial detail.

## What you must still do
- Capture and keep proof of consent on the form/ad platform.
- Import your existing opt-outs into the DNC sheet before the first call, and check numbers against the national/state DNC registries where required for your campaign type.
- Register Twilio numbers for A2P 10DLC/branded calling where required.
- Retain recordings and transcripts only as long as your privacy policy says; GoHighLevel and Vapi both store them.
- Add a human escalation path for complaints (the alert SMS numbers in `config.json`).

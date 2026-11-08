# Call scripts (human-readable)

The live system prompts are `outbound-system-prompt.md` and `inbound-system-prompt.md`. This page shows what a good call sounds like, so agents and reviewers can check the AI against it. Wording varies on every call by design.

## Outbound: new lead, goes well

**Ava:** Hi Alex, this is Ava, the virtual assistant from Prime Estates. You recently asked about the three-bed in Riverside. This call may be recorded for quality. Do you have a quick minute?
**Lead:** Sure.
**Ava:** Great. Are you looking to buy, or rent?
**Lead:** Buy.
**Ava:** Got it. Roughly what budget are you working with?
**Lead:** Around five fifty.
**Ava:** And is Riverside the area you have in mind, or are you open to others?
**Lead:** Riverside mostly.
**Ava:** Perfect. How many bedrooms, and how soon are you hoping to move?
**Lead:** Three, ideally within a month.
**Ava:** Okay. And are you already pre-approved, paying cash, or still sorting out financing?
**Lead:** Pre-approved.
*(save_qualification: hot, transfer available)*
**Ava:** From what you've told me it sounds like you're ready to move. Would you like me to put you through to one of our senior agents right now?
**Lead:** Actually, can I just see it first?
*(check_availability)*
**Ava:** Of course. I have Tuesday at ten a.m. or Wednesday at two p.m. Which suits you?
**Lead:** Wednesday.
*(book_viewing; SMS confirmation sent)*
**Ava:** Booked for Wednesday at two. I've just texted you the details. Anything else I can help with?
**Lead:** No, thanks.
**Ava:** Lovely, talk soon. Bye now.

## Outbound: "not now"

**Lead:** I'm driving, can you call tomorrow?
**Ava:** No problem at all. What time tomorrow works, morning or afternoon?
**Lead:** Afternoon, about three.
*(schedule_callback with local time; row added to the Callbacks sheet)*
**Ava:** Done, I'll call you tomorrow around three. Drive safe.

## Outbound: "stop calling"

**Lead:** Stop calling me.
**Ava:** Understood, I'm sorry to have bothered you. I've taken you off our list and you won't hear from us again.
*(opt_out: DNC row, GoHighLevel Do-Not-Disturb, pending callbacks cancelled; end_call)*

## Outbound: voicemail
The assistant leaves the short configured message (no property or financial details) and hangs up. A retry is queued.

## Inbound: listing question

**Ava:** Thanks for calling Prime Estates, this is Ava, the virtual assistant. This call may be recorded for quality. How can I help you today?
**Caller:** Do you have any pet-friendly two-beds for rent?
*(search_listings)*
**Ava:** I have a sunny two-bed in Riverside at twenty-one hundred a month; it's cats only. Would you like a viewing, or should I check for something with dogs allowed?

If the answer is not in the listings data the assistant says an agent will cover it, and never guesses.

## Voicemail text (default)
"Hi {{firstName}}, this is Ava from Prime Estates about your property enquiry. I'll try you again soon, or call us back on {{agency phone}}. Thanks!"

## SMS texts
- **Viewing confirmation:** "Hi {name}, your property viewing with {agency} is confirmed for {day, time} ({listing}). {meeting note} To change it, call {agency phone}. Reply STOP to opt out."
- **Reply to a STOP:** handled by Twilio (blocks further texts) and workflow 04 (DNC sheet, GoHighLevel DND, cancel callbacks).
- **Agent hot-lead alert (to your agents):** "HOT LEAD on a live AI call: {name} {phone}. {intent, budget, area, bedrooms, timeline, financing}. A live transfer may come through - be ready to answer. {GHL link}"

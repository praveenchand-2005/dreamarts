# Dreamarts WhatsApp Cloud API Setup

Add these Render environment variables:

WHATSAPP_ACCESS_TOKEN
WHATSAPP_PHONE_NUMBER_ID

Optional production webhook configuration can be added in Meta Developers.

Customer phone numbers are stored in public.profiles.phone.

For production-initiated WhatsApp messages outside the 24-hour customer service window, Meta-approved message templates should be used. The current integration is suitable for Cloud API testing and session messages; production transactional delivery should migrate to approved templates.

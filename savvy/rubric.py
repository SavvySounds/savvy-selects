"""The grading rubric.

This file is the taste of the system. Edit it as the review deck teaches you
where the grader is wrong, then re-run `savvy score`. Run `savvy calibrate`
after 20+ human ratings to see exactly what to change.
"""

RUBRIC = """You are grading a single moment from a DJ / event-production company's
footage archive. The company is Savvy Sounds Collective in Los Angeles. The clips
will be used in a corporate capabilities reel and in paid ads aimed at event
producers, brand agencies, and venue managers.

You are shown a contact strip: three frames sampled from across one continuous shot,
left to right in time order.

Score 0-10 on how usable this moment is for premium corporate marketing.

Score HIGH for:
- a full, genuinely energised crowd; bodies in motion, hands up, real reaction
- the DJ visible and looking professional and in command
- clean, well-built rig; tidy cable work; good uplighting or lighting design
- venue or brand signage, staging, or production value legibly in frame
- wide establishing shots that show the scale of a room
- sharp exposure, stable framing, colour that looks intentional

Score LOW for:
- empty or thin dance floors, people standing still or seated and disengaged
- messy load-in, road cases, gear bags, half-built stages
- motion blur, focus hunting, blown highlights, heavy grain, phone-in-face shots
- unflattering close-ups of individual guests
- anything that reads as amateur, chaotic, or like a house party
- shots where nothing is happening

Return ONLY a JSON object, no prose, no markdown fences:
{"score": <0-10 integer>,
 "tags": [<3-6 short lowercase tags, e.g. "packed floor","wide crowd","dj in frame","uplighting","brand signage","rooftop">],
 "orientation": "<one of: wide, medium, close>",
 "note": "<max 12 words on what this shot is>"}"""

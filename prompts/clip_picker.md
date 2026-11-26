You pick YouTube Shorts clips from a GTA 6 video for a clips channel. The goal: clips people watch to the end, replay and share.
Source video: {title}

Below is the timestamped transcript. Each line is `second|loudness 0-9|text`. Loudness is only a hint (screams, reactions, explosions). A quiet line can be the best moment, and a loud one can be pure noise.

A great clip:
- STARTS on the hook. Its first line grabs attention with no context: a shocking claim, a strong reaction, a question, or the start of the action. Never start on greetings, setup, filler or mid-sentence.
- ENDS right after the payoff: the punchline, reveal, reaction or outcome. Don't let it trail off.
- Is self-contained. Someone scrolling past with no context understands it.
- Has one strong emotion: funny, shocking, hype, wholesome or controversial.
- Is {lo}-{hi} seconds long, ideally about {tgt}.

Return up to {n} non-overlapping clips, best first. Leave out anything below 5/10. Returning fewer clips, or [], is fine.
Reply ONLY with JSON: [{"start": seconds, "end": seconds, "score": 0-10, "hook": "opening line, max 12 words", "reason": "why it works, max 12 words", "title": "Shorts title, max 70 chars, contains 'GTA 6', no clickbait lies", "overlay": "big on-screen text for the first 2 seconds: 2-5 words that tease the payoff without spoiling it, e.g. WAIT FOR THE ENDING / HE DID NOT EXPECT THIS / $2M CAR GONE WRONG; no emoji, no 'GTA 6'"}]

Transcript:

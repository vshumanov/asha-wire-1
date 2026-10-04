# Wire taste profile

This file steers the local decision model (`laya:en` via Ollaya). The bake reads
it on every run and folds the Likes/Dislikes below into the `interest` question
it asks about each candidate. Edit freely — plain prose is fine, one idea per
clause. A change here invalidates the score cache automatically (the file's hash
is part of the cache key), so the next bake re-scores everything.

Keep it about *taste*, not *topic*: the TECH / RETRO / CONCEPT split and the
"no news/politics" rule are handled separately. This is "given it's on-topic,
how much would I want to read it?"

Likes: hardware hacking and teardowns, retro computing and game preservation,
systems thinking, small/local/self-hosted AI, embedded and low-level
programming, repurposing old hardware, engineering mechanisms explained in
depth, computing history, e-ink / feature-phone / calm-tech culture.

Dislikes: AI hype and funding rounds, "thought leader" essays, crypto, growth
hacking, startup PR, listicles, benchmarks-as-marketing, politics, celebrity
and culture-war content.

RUN chappe bootstrap --channel $ARGUMENTS
RUN chappe doctor
RUN chappe briefing $ARGUMENTS --period 90d --budget tokens:12000
RUN chappe posts timing $ARGUMENTS --period 365d --timezone UTC
RUN chappe posts velocity $ARGUMENTS --period 365d

Summarize the JSON output into channel signals; audience demand; top posts; next commands.
Include data footprint, metric quality, timing windows, share velocity, post ids/links, audience questions, growth experiments, draftable hooks, and data limits.

If Chappe itself fails, a local patch can unblock the run. Move the fix into https://github.com/crimeacs/chappe, add a test, and propose a PR.

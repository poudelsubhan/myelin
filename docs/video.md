# One-minute submission video

The participant supplied the requirement: a short **one-minute** video highlighting
features, code and functionality built during the hackathon. The prepared video
is [demo.mp4](media/demo.mp4); [manifest](media/manifest.json) records measured
length, checksum, source IDs and narration for every scene.

All shots are explicitly labelled **RECORDED EVIDENCE · EDITED EXCERPTS**. They
combine actual recorded browser frames, actual program bindings and measured
results. The narration is generated offline by macOS; the video uses no model or
media API calls. The displayed zero-call result is an execution measurement, not
a claim that learning, compilation or model-reference validation was free.

| Seconds | Beat |
|---|---|
| 0–6 | Astra performs the invoice task |
| 6–14 | Observed traffic becomes a typed HTTP program |
| 14–22 | New input executes with zero model calls |
| 22–30 | A changed field produces a pre-write rejection |
| 30–39 | Local repair passes the full core oracle suite |
| 39–46 | Incorrect/costlier candidates are rejected |
| 46–55 | Accepted steering becomes the explicit expense rule |
| 55–60 | Concrete development fix, regression test and public repository |

To rebuild on macOS with the original private artifacts:

```sh
uv sync --locked --group media
uv run --group media python scripts/build_video.py runs/full-source.json
```

Raw run folders are intentionally private and are needed to reproduce the exact
video. A clean clone includes the completed video and sanitized evidence exports.
The supplied [submission form](https://cerebralvalley.ai/e/openai-gpt-6-astra-sf/hackathon/submit)
requires the team's submission; nothing has been enrolled, signed or submitted
by these scripts. Other authenticated-guide eligibility details remain for the
participant to confirm in that workflow.

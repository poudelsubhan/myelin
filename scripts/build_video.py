# ruff: noqa: E501
"""Make a one-minute video from real saved evidence, with offline system narration."""

import argparse
import asyncio
import base64
import hashlib
import html
import json
import re
import subprocess
from pathlib import Path

import imageio_ffmpeg
from playwright.async_api import async_playwright

from myelin.config import ROOT, Settings
from myelin.gate.ledger import CandidateStore


def duration(ffmpeg, path):
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True
    )
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", result.stderr)
    if not m:
        raise ValueError("Encoder could not inspect media duration")
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])


async def main(source_path):
    source = json.loads(Path(source_path).read_text())
    settings = Settings.load()
    core, ext = source["core"], source["extensions"]
    output = ROOT / "docs/media"
    output.mkdir(parents=True, exist_ok=True)
    work = (settings.runs_dir / "submission-video").resolve()
    work.mkdir(parents=True, exist_ok=True)
    candidates = CandidateStore(ROOT / "programs")
    learned = core["runs"]["learn"]
    repair = core["runs"]["repair"]
    ninth = core["runs"]["ninth"]
    original = candidates.get("crm.create_invoice", learned["candidate_hash"])
    patched = candidates.get("crm.create_invoice", repair["candidate_hash"])
    invoice = next(s for s in original.steps if s.id == "invoice-write")
    fixed = next(s for s in patched.steps if s.id == "invoice-write")
    steered = ext["expense"]["runs"]["steered"]
    branch = next(
        s
        for s in candidates.get("expense.submit_expense", ext["expense"]["steered_hash"]).steps
        if s.kind == "branch"
    )

    def frame(rid, term=None):
        trace = json.loads((settings.runs_dir / rid / "trace.json").read_text())
        actions = [a for a in trace["actions"] if a["outcome"] == "success"]
        selected = next(
            (a for a in reversed(actions) if term and term.lower() in str(a.get("target")).lower()),
            actions[-1],
        )
        return settings.runs_dir / rid / "observations" / (selected["after_id"] + ".png")

    scenes = [
        (
            6,
            "Give it a task.",
            "Create an invoice. Mark it paid.",
            frame(learned["run_id"], "create invoice"),
            f"Recorded browser task\n{learned['result']['model_calls']} navigation responses\nIndependent business oracle: PASS",
            learned["run_id"],
            "Give Myelin a task. Astra operates the browser and completes it once.",
        ),
        (
            8,
            "The run becomes a program.",
            "Observed requests. Typed inputs. Fresh identity.",
            None,
            "POST "
            + str(invoice.url.model_dump())
            + "\n\n"
            + "\n".join(
                k
                + " = "
                + (
                    v.kind + "." + v.key
                    if hasattr(v, "key")
                    else "input.quantity * input.unit_price_cents"
                    if v.kind == "multiply_int"
                    else str(v.value)
                )
                for k, v in invoice.body.items()
            ),
            learned["candidate_hash"],
            "Myelin compiles the successful trace into a program, binding new inputs, fresh authentication, and response derived identifiers.",
        ),
        (
            8,
            "New input. Zero model calls.",
            "The saved program executes the task.",
            None,
            f"NEW TASK: {ninth['result']['run_id']}\n\nModel calls: 0\nModel spend: $0\nWork units: {ninth['result']['work_units']}\nExecution: {ninth['result']['wall_ms']} ms\n\nInvoice + payment oracle: PASS",
            ninth["run_id"],
            "A new invoice runs with zero model calls. These measurements and the successful payment check come from the recorded execution.",
        ),
        (
            8,
            "Then the app changes.",
            "A renamed request field causes a pre-write rejection.",
            frame(repair["run_id"], "create invoice"),
            "HTTP 422\nFailed operation: invoice-write\nEffect: not applied\nCompleted prefix retained\n\nVisual repair resumes at this checkpoint.",
            repair["run_id"],
            "When the app renames a request field, Myelin detects a rejected write and resumes visual repair from the checkpoint.",
        ),
        (
            9,
            "Repair locally. Verify globally.",
            "The earlier steps stay unchanged.",
            None,
            "BEFORE\n"
            + ", ".join(invoice.body)
            + "\n\nAFTER\n"
            + ", ".join(fixed.body)
            + "\n\n8 / 8 fresh business checks passed\nRestoration: PROMOTED\nNext input: zero model calls",
            repair["gate_id"],
            "Only the broken operation changes. Eight fresh cases check the repair before promotion. The next input again runs without model calls.",
        ),
        (
            7,
            "Incorrect patches stay out.",
            "Passing a patch tool is not passing validation.",
            None,
            "Wrong amount             REJECTED\nMissing payment          REJECTED\nDuplicate write          REJECTED\nUnnecessary extra work   REJECTED\n\nHosted analysis and staged patching\nare also gated before adoption.",
            core["demo_id"],
            "Wrong values, duplicate writes, and unnecessary work are rejected. Even a hosted, staged patch must pass validation before adoption.",
        ),
        (
            9,
            "An instruction becomes a rule.",
            "“Over $500, add manager note approved by demo.”",
            frame(steered["run_id"], "submit expense"),
            'if amount_cents > 50000:\n    manager_note = "approved by demo"\n\nBelow / equal / above: PASS\n8 program + 8 model comparisons\nSource: '
            + branch.source_steer_id,
            steered["run_id"],
            "Live steering becomes an explicit rule. The expense program passes below, equal, and above five hundred dollars against eight fresh model references.",
        ),
        (
            5,
            "Built with inspectable evidence.",
            "Two owned apps. Public code. Recorded proofs.",
            None,
            "DEVELOPMENT FIX\nKeep original goal AND repair context.\n\nRegression test:\ntest_repair_retains_original_policy_and_inputs\n\ngithub.com/poudelsubhan/myelin",
            "6d98fad · tests/test_recorder.py",
            "The public repository includes the implementation, development fixes, regression tests, and measured evidence.",
        ),
    ]
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    manifests, elapsed = [], 0
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={"width": 1280, "height": 720}, device_scale_factor=1
        )
        for i, (seconds, title, subtitle, picture, detail, source_id, narration) in enumerate(
            scenes
        ):
            pic = (
                (
                    '<img src="data:image/png;base64,'
                    + base64.b64encode(picture.read_bytes()).decode()
                    + '">'
                )
                if picture
                else ""
            )
            page_html = (
                """<html><style>*{box-sizing:border-box}body{margin:0;background:#101b18;color:#edf5ef;font:20px Arial}header{padding:28px 48px;border-bottom:1px solid #385043;display:flex;justify-content:space-between;letter-spacing:3px}.badge{color:#eedb93;font:14px Arial;letter-spacing:1px}main{padding:32px 48px}h1{font-size:43px;letter-spacing:-1.5px;margin:0 0 10px}h2{font-size:22px;color:#b4ccb9;font-weight:400;margin:0 0 26px}.content{display:flex;gap:24px;height:395px}.visual{min-width:0;flex:1.25;background:#edf5ef;border-radius:8px;overflow:hidden}.visual img{width:100%;height:100%;object-fit:contain}pre{min-width:0;flex:1;white-space:pre-wrap;overflow-wrap:anywhere;font:18px/1.55 monospace;margin:0;padding:22px;border:1px solid #385043;border-radius:8px;color:#c9ecbe;overflow:hidden}footer{position:absolute;bottom:25px;left:48px;right:48px;color:#9bb3a2;font:13px monospace}.progress{position:absolute;bottom:0;height:5px;background:#c9ecbe}</style><body><header><b>MYELIN</b><span class="badge">RECORDED EVIDENCE · EDITED EXCERPTS</span></header><main><h1>"""
                + html.escape(title)
                + "</h1><h2>"
                + html.escape(subtitle)
                + '</h2><div class="content">'
                + ('<div class="visual">' + pic + "</div>" if pic else "")
                + "<pre>"
                + html.escape(detail)
                + "</pre></div></main><footer>SOURCE "
                + html.escape(source_id)
                + '</footer><div class="progress" style="width:'
                + str((elapsed + seconds) / 60 * 100)
                + '%"></div></body></html>'
            )
            await page.set_content(page_html)
            await page.evaluate("Promise.all([...document.images].map(i=>i.decode()))")
            shot = work / f"{i:02}.png"
            await page.screenshot(path=str(shot))
            textfile, voice = work / f"{i:02}.txt", work / f"{i:02}.aiff"
            textfile.write_text(narration)
            subprocess.run(
                [
                    "/usr/bin/say",
                    "-v",
                    "Samantha",
                    "-r",
                    "175",
                    "-f",
                    str(textfile),
                    "-o",
                    str(voice),
                ],
                check=True,
            )
            voice_seconds = duration(ffmpeg, voice)
            speed = max(1, voice_seconds / (seconds - 0.25))
            clip = work / f"{i:02}.mp4"
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-loglevel",
                    "error",
                    "-loop",
                    "1",
                    "-i",
                    str(shot),
                    "-i",
                    str(voice),
                    "-vf",
                    "scale=1280:720,format=yuv420p",
                    "-af",
                    f"atempo={speed},apad",
                    "-t",
                    str(seconds),
                    "-r",
                    "30",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "21",
                    "-c:a",
                    "aac",
                    "-ar",
                    "48000",
                    str(clip),
                ],
                check=True,
            )
            manifests.append(
                {
                    "start_seconds": elapsed,
                    "duration_seconds": seconds,
                    "title": title,
                    "source_id": source_id,
                    "narration": narration,
                }
            )
            elapsed += seconds
        await browser.close()
    concat = work / "clips.txt"
    concat.write_text(
        "".join("file '" + str(work / f"{i:02}.mp4") + "'\n" for i in range(len(scenes)))
    )
    final = output / "demo.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat),
            "-t",
            "60",
            "-vf",
            "setpts=PTS-STARTPTS",
            "-af",
            "asetpts=PTS-STARTPTS",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "21",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(final),
        ],
        check=True,
    )
    manifest = {
        "presentation": "recorded_evidence_replay",
        "duration_seconds": duration(ffmpeg, final),
        "sha256": hashlib.sha256(final.read_bytes()).hexdigest(),
        "scenes": manifests,
        "audio": "Offline macOS Samantha narration; no model/media API used",
        "new_model_calls": 0,
        "source_core_demo": core["demo_id"],
        "source_extension_proofs": {
            "steering": steered["run_id"],
            "hosted": ext["hosted"]["run_id"],
            "native_async": ext["native"]["run_id"],
        },
    }
    assert 59 <= manifest["duration_seconds"] <= 60.1
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(
        json.dumps(
            {
                "video": str(final),
                "duration_seconds": manifest["duration_seconds"],
                "new_model_calls": 0,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    asyncio.run(main(parser.parse_args().source))

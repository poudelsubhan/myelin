# ruff: noqa: E501
"""Build a 60-second narrated video from actual live footage and console evidence."""

import hashlib
import json
import subprocess

import imageio_ffmpeg

from myelin.config import ROOT


def main():
    work = ROOT / ".local/myelin/video"
    output = ROOT / "docs/media"
    output.mkdir(exist_ok=True)
    frames = json.loads((work / "execution-frames.json").read_text())
    console = json.loads((work / "console-frames.json").read_text())
    result = json.loads((work / "stage-result.json").read_text())
    assert result["status"] == "verified" and result["model_calls"] == 0
    assert json.loads((work / "fresh-readback.json").read_text())["status"] == "verified"
    ff = imageio_ffmpeg.get_ffmpeg_exe()

    def run(args):
        subprocess.run([ff, "-y", "-loglevel", "error", *args], check=True)

    scenes = [
        (
            8,
            "Astra learns. Myelin reuses.",
            "Real Trello account · independently verified business outcomes",
            [(str(work / "final-card.png"), 1)],
            "This is Myelin in a real Trello account. GPT six Astra learns the workflow; Myelin turns it into a reusable, checked program.",
        ),
        (
            14,
            "Fresh input → verified outcome",
            "Recorded live execution · 20.765 seconds · 0 model calls · sped up",
            [
                (
                    f["path"],
                    max(0.03, frames[i + 1]["time"] - f["time"]) if i + 1 < len(frames) else 0.4,
                )
                for i, f in enumerate(frames)
            ],
            "This is a fresh recorded execution, sped up. It creates the card, saves the brief and date, adds the checklist, and verifies sixteen business assertions. Twenty-one seconds. Zero model calls.",
        ),
        (
            10,
            "Five tasks. Same links. Zero calls.",
            "Actual console resubmission · no additional cards created",
            [(console["batch-inputs"], 2), (console["batch-results"], 8)],
            "The console also replays five completed tasks. Every row returns its original link with zero calls and no extra cards.",
        ),
        (
            8,
            "The teacher is GPT-6 Astra.",
            "Recorded learning evidence · goal → browser actions → reusable program",
            [(console["learning-" + str(i)], 2) for i in range(4)],
            "These are Astra’s recorded browser actions. Inputs and effects become reusable bindings, while the model handles discovery.",
        ),
        (
            10,
            "Evidence becomes executable code.",
            "Journal before dispatch · fresh authentication · one-call scoped repair",
            [(console["effects-code"], 5), (console["repair-code"], 5)],
            "Before dispatch, the journal commits each effect key. An observed write uses fresh authentication. Astra repaired one induced locator failure; two new canaries passed.",
        ),
        (
            10,
            "Built with Astra. Checked with evidence.",
            "102 tests pass · 54 immutable programs preserved · github.com/poudelsubhan/myelin",
            [(console["async-code"], 4), (console["steering-code"], 4), (console["console"], 2)],
            "We built and debugged Myelin with Astra. Our original demos verify native asynchronous tools, live steering, and hosted patching. One hundred two tests pass; fifty-four program hashes stay unchanged.",
        ),
    ]
    elapsed = 0
    manifest = []
    for i, (seconds, title, subtitle, pictures, narration) in enumerate(scenes):
        total = sum(t for _, t in pictures)
        concat = work / f"scene-{i}.txt"
        concat.write_text(
            "".join(f"file '{path}'\nduration {t / total * seconds:.8f}\n" for path, t in pictures)
            + f"file '{pictures[-1][0]}'\n"
        )
        titlefile = work / f"title-{i}.txt"
        titlefile.write_text(title)
        subfile = work / f"subtitle-{i}.txt"
        subfile.write_text(subtitle)
        spoken = work / f"voice-{i}.txt"
        spoken.write_text(narration)
        voice = work / f"voice-{i}.aiff"
        subprocess.run(
            ["/usr/bin/say", "-v", "Samantha", "-r", "175", "-f", str(spoken), "-o", str(voice)],
            check=True,
        )
        import re

        info = subprocess.run([ff, "-i", str(voice)], capture_output=True, text=True).stderr
        match = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", info)
        length = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
        speed = max(1, length / (seconds - 0.2))
        filters = (
            "scale=1280:628:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:0:color=0x10151e,setsar=1,"
            f"drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:textfile={titlefile}:fontsize=28:fontcolor=white:x=28:y=641,"
            f"drawtext=fontfile=/System/Library/Fonts/Supplemental/Arial.ttf:textfile={subfile}:fontsize=16:fontcolor=0x9de4d4:x=28:y=681,format=yuv420p"
        )
        clip = work / f"clip-{i}.mp4"
        run(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat),
                "-i",
                str(voice),
                "-vf",
                filters,
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
                "20",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                str(clip),
            ]
        )
        manifest.append(
            {
                "start_seconds": elapsed,
                "duration_seconds": seconds,
                "title": title,
                "narration": narration,
            }
        )
        elapsed += seconds
    clips = work / "clips.txt"
    clips.write_text("".join(f"file '{work / f'clip-{i}.mp4'}'\n" for i in range(len(scenes))))
    final = output / "live-demo.mp4"
    run(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(clips),
            "-t",
            "60",
            "-vf",
            "fps=30",
            "-af",
            "apad,atrim=duration=60",
            "-frames:v",
            "1800",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(final),
        ]
    )
    metadata = {
        "duration_seconds": 60,
        "file": "live-demo.mp4",
        "sha256": hashlib.sha256(final.read_bytes()).hexdigest(),
        "type": "actual live execution and console footage; recorded learning clearly labelled",
        "stage_run_id": result["run_id"],
        "stage_wall_ms": result["wall_ms"],
        "stage_model_calls": 0,
        "stage_assertions": 16,
        "video_frames": 1800,
        "fps": 30,
        "scenes": manifest,
        "narration": "offline macOS Samantha; no media API calls",
        "source_learning": "927a12b5-c3ef-41e3-98c3-4a90eb76f395",
        "source_repair_candidate": result["candidate_hash"],
    }
    (output / "live-manifest.json").write_text(json.dumps(metadata, indent=2))
    run(
        [
            "-i",
            str(final),
            "-vf",
            "fps=1/10,scale=640:-1,tile=3x2",
            "-frames:v",
            "1",
            str(work / "contact-sheet.png"),
        ]
    )
    print(final)


if __name__ == "__main__":
    main()

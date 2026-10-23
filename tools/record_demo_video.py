#!/usr/bin/env python3
"""Record a narrated synthetic walkthrough on a fresh, disposable office.

Requires Node/Playwright with Chromium, and FFmpeg with libx264, libass and flite.
The existing presentation database, vault, cases and receiver remain untouched.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
import wave

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCENES = [
    ("intro", "WELCOME", "From documents to a verified handoff.",
     "Welcome to LexFlow. This synthetic demonstration follows a property purchase from incoming documents to human review, approved delivery, and office analytics."),
    ("overview", "01 / OFFICE WORKFLOW", "One queue shows the work that still needs a person.",
     "The office overview follows seven visible stages. The work queue brings together cases that need classification, review, approval, or transfer."),
    ("upload", "02 / RECEIVE DOCUMENTS", "Upload the three sample PDFs as one intake.",
     "Start a new intake with the buyer questionnaire, register extract, and draft contract. Every person, address, and register entry in this video is fictional."),
    ("process", "03 / CLASSIFY AND EXTRACT", "A disagreement blocks approval.",
     "LexFlow identifies the property purchase and proposes structured values. Here the documents disagree on the land register sheet, so approval is blocked."),
    ("evidence", "04 / FOLLOW THE EVIDENCE", "Compare the proposed value with its source.",
     "Select the land register field to locate its source evidence. The questionnaire and register say sheet eight twelve. The draft contract says eight seventy two."),
    ("resolve", "05 / RESOLVE THE CONFLICT", "Choose the register's value for this synthetic case.",
     "The clerk compares the candidates and chooses the register extract. The selected value is recorded, and the unresolved conflict is removed."),
    ("confirm", "06 / COMPLETE HUMAN REVIEW", "The purchase price requires explicit confirmation.",
     "Office policy requires a person to confirm the purchase price. Once all blockers are cleared, the case becomes ready for approval."),
    ("notary", "07 / AUTHORISED APPROVAL", "Switch from the clerk to the notary.",
     "Approval belongs to the notary or another authorised approver. The clerk can review the case, but cannot approve this transfer."),
    ("approve", "08 / FREEZE THE DECISION", "Approval freezes the values and destination mappings.",
     "The notary approves the reviewed matter. LexFlow freezes the values, destination mappings, and approval identity in an auditable snapshot."),
    ("export", "09 / CREATE THE EXPORT", "Export creation is separate from confirmed delivery.",
     "Choose the HTTP reference receiver and create an approved export. The export is bound to the frozen approval, and still requires a receipt."),
    ("send", "10 / AUTHENTICATED HANDOFF", "Send the approved payload to the reference destination.",
     "The notary explicitly sends the approved payload. The independent reference receiver validates the signed request and commits the received data before responding."),
    ("receipt", "11 / VERIFY THE RECEIPT", "A verified signed receipt records delivery.",
     "A matching signed receipt confirms delivery. Retries reuse the same approval bound identity to avoid duplicates. This reference receiver is separate from any real vendor integration."),
    ("audit", "12 / TRACE THE OUTCOME", "Review, approval, export and delivery are auditable.",
     "Open the audit trail for this case. Review, approval, export, and receipt events are traceable, and the office audit chain is verified."),
    ("scan", "13 / HANDLE SCANNED SOURCES", "OCR proposals still require original-source review.",
     "Scanned documents can be read locally. The proposed values remain subject to review. Approval stays blocked until someone checks the original and acknowledges the source."),
    ("analytics", "14 / FILTER THE REPORT", "Keep synthetic and live-office measurements separate.",
     "Pilot analytics uses one consistent set of receipt dates, workflow, source, and data cohort filters. The six summary cards describe the same selected intake cohort."),
    ("quality", "15 / EXTRACTION AND REVIEW", "First extraction stays distinct from human-completed data.",
     "Inspect initial extraction coverage, current field states, changed values, and pending source acknowledgements. Confidence scores are heuristic proposals, not measured customer accuracy."),
    ("workload", "16 / WORKLOAD AND TIMING", "Turnaround includes waiting; it does not measure staff effort.",
     "Workload reporting shows case aging, source breakdowns, and median and ninetieth percentile turnaround with sample counts. Elapsed approval time includes waiting, not just staff handling."),
    ("delivery", "17 / DELIVERY AND AUDIT", "Separate pending exports, failures and verified receipts.",
     "Delivery reporting separates pending, failed, and delivered exports, with connector reliability and audit coverage. Each chart also provides an exact value table, and the filtered report can be exported."),
    ("roadmap", "18 / EXPAND INTEGRATIONS", "Native plugins and actual vendor adapters remain planned.",
     "The next integrations are native Outlook intake and one office selected vendor adapter, followed by Word and cloud sources. These remain planned work, with destination acceptance required."),
    ("outro", "LEXFLOW AI", "Receive. Review. Approve. Verify. Measure.",
     "LexFlow keeps evidence, human decisions, verified handoffs, and reporting together. Use the included sample documents and updated guide to present this controlled workflow."),
]


def run(command, *, env=None, **kwargs):
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, **kwargs)
    if result.returncode:
        # Server/seed stdout can contain fresh private tokens. Never echo it.
        if len(command) > 1 and command[1] == "manage.py":
            raise RuntimeError(f"The isolated {command[2]} step failed; no presentation data was changed.")
        raise RuntimeError(result.stderr[-5000:] or "A recording tool failed.")
    return result


def probe(path):
    return json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-show_chapters", "-of", "json", str(path)]).stdout)


def timestamp(value, *, ass=False):
    if ass:
        ticks = round(value * 100)
        return f"{ticks // 360000}:{ticks // 6000 % 60:02d}:{ticks // 100 % 60:02d}.{ticks % 100:02d}"
    ticks = round(value * 1000)
    return f"{ticks // 3600000:02d}:{ticks // 60000 % 60:02d}:{ticks // 1000 % 60:02d},{ticks % 1000:03d}"


def escape_ass(value):
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def compose(runtime, timeline, output, *, voice):
    entries = timeline["scenes"]
    duration = float(probe(runtime / "capture.webm")["format"]["duration"])
    rate = 48000
    samples = bytearray((int((duration + 1) * rate)) * 2)
    for entry in entries:
        with wave.open(str(runtime / (entry["id"] + ".wav")), "rb") as audio:
            assert audio.getnchannels() == 1 and audio.getframerate() == rate and audio.getsampwidth() == 2
            data = audio.readframes(audio.getnframes())
        offset = round((entry["start"] + .6) * rate) * 2
        assert offset + len(data) <= round(entry["end"] * rate) * 2, "Narration must fit its scene"
        samples[offset:offset + len(data)] = data
    with wave.open(str(runtime / "narration.wav"), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(samples)

    subtitles = "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 0\n\n"
    subtitles += "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
    subtitles += "Style: Caption,DejaVu Sans,32,&H00FFFFFF,&H00FFFFFF,&H00142D2C,&H00142D2C,0,0,0,0,100,100,0,0,1,0,0,2,120,120,38,1\n"
    subtitles += "Style: Chapter,DejaVu Sans,23,&H00A1E4F4,&H00FFFFFF,&H00142D2C,&H00142D2C,-1,0,0,0,100,100,0,0,1,0,0,9,96,96,23,1\n"
    subtitles += "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    srt, transcript = [], ["LEXFLOW AI — SYNTHETIC DEMONSTRATION VIDEO", "", "English narration is synthesized locally with FFmpeg/flite (voice: " + voice + ").", "Recorded on a fresh disposable office; the existing presentation cases were not used.", ""]
    for index, entry in enumerate(entries, 1):
        start, end = entry["start"], entry["end"]
        subtitles += f"Dialogue: 0,{timestamp(start, ass=True)},{timestamp(end, ass=True)},Caption,,0,0,0,,{{\\fad(180,180)}}{escape_ass(entry['caption'])}\n"
        subtitles += f"Dialogue: 0,{timestamp(start, ass=True)},{timestamp(end, ass=True)},Chapter,,0,0,0,,{escape_ass(entry['chapter'])}\n"
        # The SRT contains the full spoken explanation; MP4 captions are concise.
        srt.append(f"{index}\n{timestamp(start + .6)} --> {timestamp(end - .25)}\n{entry['narration']}\n")
        transcript += [timestamp(start).split(",")[0] + "  " + entry["chapter"], entry["narration"], ""]
    (runtime / "captions.ass").write_text(subtitles)
    chapters = [";FFMETADATA1"]
    for entry in entries:
        chapters += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={round(entry['start'] * 1000)}",
                     f"END={round(min(entry['end'], duration) * 1000)}", "title=" + entry["chapter"]]
    (runtime / "chapters.ffmeta").write_text("\n".join(chapters) + "\n")
    output.mkdir(parents=True, exist_ok=True)
    print("Rendering the narrated 1080p MP4 with captions…", flush=True)
    # Runtime paths are generated under /tmp with no filter-special characters.
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    filters = (
        "[0:v]scale=1728:864:flags=lanczos,pad=1920:1080:96:64:color=0x142d2c,"
        f"drawtext=fontfile={font}:text='LEXFLOW AI  /  PILOT WALKTHROUGH':fontsize=23:fontcolor=white:x=96:y=23,"
        "drawbox=x=96:y=64:w=1728:h=864:color=0x426c65:t=1,"
        f"ass={runtime / 'captions.ass'},fade=t=in:st=0:d=0.5,fade=t=out:st={duration - .7:.3f}:d=0.7[v];"
        "[1:a]loudnorm=I=-16:TP=-1.5:LRA=11,afade=t=in:d=0.15,"
        f"afade=t=out:st={duration - .6:.3f}:d=0.6[a]"
    )
    movie = runtime / "LexFlow_Demonstration.mp4"
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(runtime / "capture.webm"),
         "-i", str(runtime / "narration.wav"), "-i", str(runtime / "chapters.ffmeta"),
         "-filter_complex", filters, "-map", "[v]", "-map", "[a]", "-map_chapters", "2",
         "-t", str(duration), "-c:v", "libx264", "-preset", "fast", "-crf", "21", "-pix_fmt", "yuv420p", "-r", "25",
         "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart",
         "-metadata", "title=LexFlow AI - synthetic pilot demonstration",
         "-metadata", "comment=Fictional cases; independent reference receiver; synthesized English narration.", str(movie)], timeout=600)
    info = probe(movie)
    video = next(stream for stream in info["streams"] if stream["codec_type"] == "video")
    audio = next(stream for stream in info["streams"] if stream["codec_type"] == "audio")
    assert (video["width"], video["height"], video["codec_name"]) == (1920, 1080, "h264")
    assert audio["codec_name"] == "aac"
    assert int(audio["sample_rate"]) == 48000 and audio["channels"] == 2
    assert len(info["chapters"]) == len(entries)
    assert abs(float(info["format"]["duration"]) - entries[-1]["end"]) < 2
    run(["ffmpeg", "-v", "error", "-i", str(movie), "-f", "null", "-"], timeout=300)
    poster = runtime / "LexFlow_Demonstration_Poster.jpg"
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "3", "-i", str(movie),
         "-frames:v", "1", "-update", "1", "-q:v", "2", str(poster)])
    for path in (movie, poster):
        shutil.copyfile(path, output / path.name)
    (output / "LexFlow_Demonstration.srt").write_text("\n".join(srt))
    (output / "LexFlow_Demonstration_Transcript.txt").write_text("\n".join(transcript))
    (output / "chapters.json").write_text(json.dumps(timeline, indent=2, ensure_ascii=False) + "\n")
    verification = {"passed": True, "synthetic_data_only": True, "existing_presentation_cases_untouched": True,
                    "scenes": len(entries), "duration_seconds": round(float(info["format"]["duration"]), 2),
                    "width": 1920, "height": 1080, "video_codec": video["codec_name"], "audio_codec": audio["codec_name"],
                    "narration": "local synthesized English / flite " + voice, "burned_in_captions": True,
                    "audio_sample_rate": 48000, "audio_channels": 2, "embedded_chapters": len(entries),
                    "spoken_subtitles": "LexFlow_Demonstration.srt", "full_decode_passed": True,
                    "browser_errors": timeline["browser_errors"], "workflow_checks": timeline["checks"],
                    "bytes": movie.stat().st_size}
    (output / "verification.json").write_text(json.dumps(verification, indent=2) + "\n")
    print(json.dumps(verification), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "presentation/video")
    parser.add_argument("--voice", choices=["slt", "awb", "rms", "kal"], default="slt")
    parser.add_argument("--check-only", action="store_true", help="Exercise the recording flow without rendering a movie")
    args = parser.parse_args()
    for binary in ("ffmpeg", "ffprobe", "node"):
        if not shutil.which(binary):
            parser.error(f"Install {binary} before recording.")
    from tools.presentation_demo import environment
    with tempfile.TemporaryDirectory(prefix="lexflow-video-") as folder:
        runtime = Path(folder)
        env = environment()
        env.update(DJANGO_SECRET_KEY=secrets.token_urlsafe(64), LEXFLOW_MASTER_KEY="fernet:" + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
                   LEXFLOW_DB_PATH=str(runtime / "office.sqlite3"), LEXFLOW_VAULT_ROOT=str(runtime / "vault"),
                   LEXFLOW_EXPORT_ROOT=str(runtime / "exports"), LEXFLOW_VIDEO_RUNTIME=str(runtime),
                   LEXFLOW_VIDEO_CHECK_ONLY="1" if args.check_only else "0")
        scenes = []
        for identifier, chapter, caption, narration in SCENES:
            textfile = runtime / (identifier + ".txt")
            textfile.write_text(narration)
            run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                 f"flite=textfile={textfile}:voice={args.voice}", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(runtime / (identifier + ".wav"))])
            duration = float(probe(runtime / (identifier + ".wav"))["format"]["duration"])
            scenes.append({"id": identifier, "chapter": chapter, "caption": caption, "narration": narration,
                           "seconds": max(6.5, duration + 2.0)})
        (runtime / "scenes.json").write_text(json.dumps(scenes))
        python = str(ROOT / ".venv/bin/python")
        print("Preparing a fresh, isolated synthetic office…", flush=True)
        run([python, "manage.py", "migrate", "--noinput"], env=env)
        run([python, "manage.py", "seed_lexflow", "--single-office"], env=env)
        secret = secrets.token_urlsafe(48)
        secretfile = runtime / "receiver.key"
        secretfile.write_text(secret)
        secretfile.chmod(0o600)
        receiver = subprocess.Popen([python, "integrations/reference_receiver.py", "--database", str(runtime / "receiver.sqlite3"),
                                     "--secret-file", str(secretfile), "--port", "0"], cwd=ROOT,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        app = None
        try:
            ready = json.loads(receiver.stdout.readline())
            env.update(LEXFLOW_REFERENCE_SECRET=secret, LEXFLOW_WEBHOOK_ALLOW_LOOPBACK="1",
                       LEXFLOW_WEBHOOK_ENDPOINTS=json.dumps({"reference": f'http://127.0.0.1:{ready["port"]}/v1/handoffs'}))
            run([python, "manage.py", "prepare_presentation_features"], env=env)
            with socket.socket() as probe_socket:
                probe_socket.bind(("127.0.0.1", 0))
                port = probe_socket.getsockname()[1]
            env["LEXFLOW_DEMO_URL"] = f"http://127.0.0.1:{port}"
            with (runtime / "server.log").open("w") as log:
                app = subprocess.Popen([python, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"], cwd=ROOT,
                                       env=env, stdout=log, stderr=log)
                for _ in range(100):
                    try:
                        with urlopen(env["LEXFLOW_DEMO_URL"] + "/login/", timeout=1) as response:
                            assert response.status == 200
                        break
                    except OSError:
                        if app.poll() is not None:
                            raise RuntimeError("The isolated recording server failed to start.")
                        time.sleep(.1)
                else:
                    raise RuntimeError("The isolated recording server did not become ready.")
                result = subprocess.run(["node", "tools/record_demo_browser.cjs"], cwd=ROOT, env=env, timeout=600)
                if result.returncode:
                    raise RuntimeError("Recording failed; the existing presentation database was not changed.")
            timeline = json.loads((runtime / "timeline.json").read_text())
            if not args.check_only:
                compose(runtime, timeline, args.output_dir.resolve(), voice=args.voice)
            else:
                print(json.dumps({"recording_flow_passed": True, "scenes": len(timeline["scenes"]), "checks": timeline["checks"]}), flush=True)
        finally:
            if app is not None:
                app.terminate()
                app.wait(timeout=10)
            receiver.terminate()
            receiver.wait(timeout=10)


if __name__ == "__main__":
    main()

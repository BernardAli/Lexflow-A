LEXFLOW AI — SAMPLE DEMONSTRATION VIDEO

Open LexFlow_Demonstration.mp4 in a browser or a standard MP4 player. The video
uses actual application screens from a fresh, disposable synthetic office.
Existing presentation cases and the development database were not used.

Version note — 30 October 2026
-----------------------------
This recording predates the structured case and mapping-profile update. It does
not demonstrate Case details, repeated participants/properties, the approved-case
API or the adapter availability list. Its video, subtitles, transcript, chapters
and verification.json retain the earlier recording's content and evidence.

The current build implements canonical case JSON and frozen mapping revisions.
ProNotar32 import remains unavailable until its contract and adapter are validated.
Use ../START_HERE.txt for the current live extension walkthrough and
../../integrations/PRONOTAR32_READINESS.txt for the implementation boundary.
The existing recorder still follows the core workflow; re-running it alone does
not add a demonstration of the new case-details screens.

Contents
--------
LexFlow_Demonstration.mp4             1080p H.264/AAC video with English voiceover
                                     and burned-in summary captions.
LexFlow_Demonstration.srt             Optional full spoken subtitles; load these
                                     in your player for the narration wording.
LexFlow_Demonstration_Transcript.txt  Narration text and chapter start times.
LexFlow_Demonstration_Poster.jpg      Opening title frame for previews.
chapters.json                        Detailed actual scene timing and narration.
verification.json                    Media format/decode and workflow checks.

The narration is synthesized locally with FFmpeg/flite (English, voice slt).
No external voice service, private office data or vendor account was used.

The walkthrough
---------------
1. Office overview and incoming synthetic PDF documents.
2. Conflicting land-register sheet, source evidence and clerk review.
3. Notary approval and frozen case values/mappings.
4. Approved HTTP export, explicit send and a verified signed receipt.
5. Case audit trail and the mandatory review of scanned originals.
6. Summary, extraction/review, workload/timing and delivery/audit reports.
7. Clearly labelled native-plugin and real-vendor integration roadmap.

The receiver is an independent synthetic reference destination. Its receipt
demonstrates LexFlow's transport contract, not a named vendor connection. Office
metrics in the recording are illustrative. Elapsed time includes waiting and
does not establish staff-time savings or customer extraction accuracy.

Reproduce
---------
Install the project's Python dependencies, Node/Playwright with Chromium, local
OCR dependencies and FFmpeg with libx264, libass and flite. From the project root:

  .venv/bin/python tools/record_demo_video.py --check-only
  .venv/bin/python tools/record_demo_video.py

If Playwright is not on Node's normal search path, set LEXFLOW_PLAYWRIGHT_MODULE
to the installed Playwright package directory. Optional LEXFLOW_CHROMIUM_EXECUTABLE
selects a browser executable. --output-dir chooses another artifact directory.

The recorder creates a new database, vault, export folder and reference receiver
under a private /tmp directory, then removes the runtime after recording. It makes
no changes to existing presentation matters. The MP4 uses manual scene pacing,
actual UI interactions and real role/approval/receipt checks; short production
title cards and a cursor indicator are added for clarity. Final encoding burns
captions into a branded frame and mixes the separately synthesized narration.

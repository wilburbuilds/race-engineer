# Race Engineer for macOS

a fork of my brother John's Race Engineer

Installed app: `/Applications/Race Engineer.app`. A shortcut is on the Desktop.

Double-click the app to start the dashboard and recorder. No Terminal, uv, separate Python installation, or browser is needed. Closing the window or choosing **Race Engineer → Quit and Save** flushes the recording and stops the backend. **Race Engineer → Open Recordings** opens the saved sessions.

Recordings and editable corner names live in `~/Documents/Race Engineer/`. Existing sessions were copied from `~/Documents/Projects/race-engineer/sessions`; the originals remain there. Keep the old command-line recorder stopped when using this app, because both listen on UDP port 20777.

This build includes the dashboard, You vs others tab, recorder, and Python 3.11 runtime for Apple Silicon. Voice is off, matching the previous launcher. It requires macOS 13 or newer. It is locally signed for this Mac, not notarized for public distribution.

Source: `App.swift` is the native Cocoa/WebKit shell. `app_backend.py` manages recording and graceful shutdown; `bootstrap.py` loads bundled code in Python isolated mode. `build.py` assembles and locally signs the app using the installed uv-managed Python runtime as the build input. Runtime operation does not depend on that external installation.

Validation completed: Swift build, code-sign verification, comparison tests, packet layout checks, bundled-runtime startup, HTTP dashboard, duplicate instance prevention, synthetic UDP recording and flush on parent EOF, and native window displaying actual Baku telemetry.

Logs: `~/Library/Logs/Race Engineer/app.log`.

## Opponent lap overlays

In **You vs others**, choose an opponent, your lap, and their lap. Speed, throttle, and brake graphs share distance from the start line. Select a sector to zoom and move the shared cursor to compare inputs at a corner. Both laps must contain recorded data in the selected section.

The recorder retains the fastest clean lap and the six most recent laps per driver at up to 10 Hz, with a 6,000-sample cap per lap. It labels incomplete, invalid, pit, and interrupted laps. Opponent traces are stored locally in each session’s `opponents.json`; they are excluded from GitHub with the recordings directory. Older recordings cannot be reconstructed, and Time Trial ghosts provide timing comparisons only.

## Beginner coaching (1.3)

The **Engineer** tab is the default home. It shows a next action, clean-lap eligibility,
a session debrief, uncertainty, and a link to the relevant driving comparison. Saved
sessions are analysed when opened. Each analysed recording gets `coaching.json` and
`debrief.md`; feedback and setup tests are stored in `coaching-state.json`.

During live free practice, record three comparable clean timed laps. The engineer reviews
repeated input cues; you can also select a recurring handling symptom. When a suggestion
appears, choose **Start this test**. Apply the displayed change in the game.
Telemetry confirms it; after three comparable test laps the engineer compares median pace
and recommends keeping provisionally, reverting, or collecting more evidence. Only one
setting is tested at a time. Feedback and setup tests are disabled in saved playback and
outside practice. Legacy recordings have no verifiable setup history.

The recorder saves setup revisions, lap conditions, and an incident timeline. Coaching
excludes invalid, pit, damaged, interrupted, unusually slow, and incomplete laps. Comparisons
check compound, fuel (within 3 kg), tyre age (within 3 laps), weather, track temperature
(within 2°C), assists, and setup revision. Those are conservative matching rules, not a
physics-based fuel or tyre correction. The fastest clean opponent trace is retained in
addition to the most recent six laps.

### Grok explanations

Install and sign into the Grok CLI (`grok login`). The app discovers `grok` on PATH or at
`~/.grok/bin/grok`. It requests schema-constrained JSON with CLI tools, subagents, and web
search disabled. Calculated telemetry evidence is sent to Grok for a plain-language
explanation, using low reasoning effort and a dedicated coaching prompt; no automatic setup edits are made. Requests happen after three additional
eligible laps, session completion, feedback, or manual refresh. AI text states the lap
count it is based on. Recording and calculated coaching continue if Grok is unavailable.
A separate background worker applies a 90-second request timeout. Closing the app saves
a calculated debrief without waiting for the model. Set `RACE_ENGINEER_GROK=off` to disable
CLI requests (also used by integration tests).

## Visual coaching and review

The Engineer view puts session metrics and one next action first. A track graphic highlights the measured focus section; it falls back to a distance diagram when position data is unavailable. The lap timeline shows relative pace, coaching eligibility, pit vicinity, and recorded tyre stints. Select a lap to see its time and exclusion reasons.

Practice setup tests show the current and proposed values with baseline, change, test, and decision stages. Full notes, classification, lap tables, and evidence remain in expandable sections. Navigation groups overview and analysis screens; charts use colour and line legends, and settings are grouped into cards.

## Community starting setups and practice tuning

The Engineer screen looks up a published F1Laps community setup for the detected track, 2025 or 2026 car regulations, and dry or wet conditions. Choose Qualifying pace or Race stint in free practice. Lookup prefers that session type and matching traction control and ABS, compares up to three published candidates, and labels fallbacks and assist differences. This is a starting point, not a claim of forum consensus or the ideal setup for a particular career car. Published lap times are self-reported.

The card shows key settings at a glance. Expand Setup menu for every published setting in game menu order, with recorded → proposed values. Missing engine braking is explicitly left at the current value. Apply settings manually in the garage. The recorder confirms actual setup changes from telemetry.

After three comparable clean laps on the current recorded setup, the coach reviews repeated input cues automatically. More steering with lower corner speed can support a small front-wing experiment; repeated throttle back-offs can support a differential experiment. Neither cue proves understeer or wheelspin. Explicit handling feedback takes priority. If the evidence has no clear cue, the coach asks how the car feels instead of inventing a change. Tests retain the baseline, wait for the change to be detected, and compare three subsequent matched laps. Results show median pace and the fastest-lap difference.

Online lookup uses its own background worker, bounded requests, and a seven-day cache in Documents/Race Engineer/setup-baselines. Offline lookup preserves cached values with an age warning. Refresh retries the source, with a one-minute request throttle. Unknown layouts, car classes or conditions do not receive guessed setups. Lookup sends track, car year, conditions and assist filters to the public source; lap telemetry is not sent there.

## Build and package

Run `python3 package.py` to build the app, create the versioned Apple Silicon ZIP in `dist/`, verify ZIP integrity and the extracted app signature, and write a SHA-256 checksum. Build prerequisites are the Xcode command-line tools and the uv-managed CPython 3.11.16 Apple Silicon runtime used by `build.py`. Distribution files contain the app and its runtime; recordings, local caches, CLI authentication, and logs are not bundled.

Release downloads are available in [GitHub Releases](https://github.com/wilburbuilds/race-engineer/releases). See [release notes](RELEASE_NOTES.md) for the current package and verification details.

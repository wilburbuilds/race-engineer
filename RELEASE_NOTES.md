# Race Engineer 1.5.0

## What's included

- Visual coaching home with one next action, a highlighted track section, lap rhythm, tyre stints, and expandable evidence.
- Written session debriefs and beginner-friendly driving explanations through the signed-in Grok CLI.
- Community starting setups from F1Laps matched to track, 2025 or 2026 F1 car regulations, and dry or wet conditions. Qualifying pace and race-stint goals are available in practice.
- Setup values in game menu order, with differences from the recorded car setup and source links. Source assist differences and missing settings are labelled.
- Automatic setup review after three comparable clean laps. Supported cues lead to one small experiment; handling feedback takes priority.
- Telemetry confirmation of setup changes, followed by matched test laps and median/fastest-lap comparisons.
- Setup history, lap conditions, incident recording, and retention of each opponent's fastest clean lap alongside recent laps.
- Background coaching and setup lookup, cached source values, and continued recording when online services are unavailable.

## Install

Download `Race-Engineer-1.5.0-macOS-arm64.zip`, unzip it, and copy **Race Engineer.app** into **Applications**, replacing the previous version after quitting it. Existing recordings remain in **Documents/Race Engineer**.

The app bundles Python 3.11 and requires Apple Silicon and macOS 13 or newer. It is ad hoc signed, not notarized. Grok explanations require the Grok CLI to be installed and signed in; telemetry and calculated coaching work independently.

Apply setup settings manually in the game. Published community setups are starting points, and telemetry-based suggestions are experiments rather than confirmed mechanical diagnoses.

## Verification

- 36 unit tests covering coaching, comparison, opponent traces, source validation, automatic setup review, and offline cache recovery.
- HTTP practice workflow covering goal selection, proposal, setup confirmation, test evaluation, and saved debrief.
- Bundled-runtime integration covering dashboard startup, UDP recording, duplicate prevention, saved recordings, and graceful shutdown.
- Live Australia source lookup and visual verification of setup values in the app.
- ZIP integrity, extracted app signature, and packaged backend imports.

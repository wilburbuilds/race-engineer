# Race Engineer for macOS

Race Engineer is a local companion app for EA SPORTS F1 gameplay. It records your laps, helps you see where you lose time, and suggests what to try next—from a driving adjustment to a small setup change during free practice.

The dashboard turns telemetry into visual coaching: a next action, a highlighted track section, lap timelines, and comparisons of speed, throttle, and braking. You can review saved sessions after the game is closed.

**[Download the latest release](https://github.com/wilburbuilds/race-engineer/releases/latest)** · [Release notes](RELEASE_NOTES.md)

## What you can do

- **Start practice with a community setup.** Get a published starting setup for the detected track and conditions, displayed in the order of the game's setup menu.
- **Tune after three clean laps.** Test one change, record another three comparable laps, and see whether pace improved.
- **Find time in your driving.** Compare your inputs with another lap or an opponent's recorded lap.
- **Learn from each session.** Review pace, consistency, handling feedback, and a suggested focus for the next session.

You apply setup changes yourself in the game. Race Engineer watches the telemetry to confirm them.

## Requirements and installation

- An **Apple Silicon Mac** running **macOS 13 or newer**.
- An EA SPORTS F1 game version that offers **UDP Format 2026**. This build accepts that telemetry format; earlier UDP formats are unsupported.
- Your Mac and game device on the **same local network**. The app has been used with Xbox gameplay.

Download the macOS arm64 ZIP, unzip it, and drag **Race Engineer.app** into **Applications**. Open the app and allow local-network access if macOS asks. The packaged app includes its Python runtime, so normal use needs no Terminal, separate Python installation, or browser.

The current release is ad hoc signed and is not notarized. If macOS blocks the first launch, follow Apple's [guide to opening apps on your Mac](https://support.apple.com/en-sg/102445).

To update, quit the old app and replace it in Applications. Your recordings are stored separately in Documents.

## Connect the game

In the game's **Settings → Telemetry**, use:

| Setting | Value |
| --- | --- |
| UDP Telemetry | On |
| UDP Broadcast Mode | On |
| UDP Port | 20777 |
| UDP Format | 2026 |
| UDP Send Rate | 20 Hz or higher |

Open Race Engineer, enter a practice or race session, and drive. The status bar shows when live telemetry is arriving; the app records automatically.

If broadcast does not reach your Mac, turn broadcast off and set the game's **UDP IP Address** to your Mac's local network IP address. You can find that address in macOS System Settings under your network connection's details.

## Your first free-practice session

### 1. Choose a starting setup

Open **Engineer** and choose **Qualifying pace** or **Race stint**. The starting-setup card looks up a published [F1Laps community setup](https://www.f1laps.com/) for the detected track, car regulations, and dry or wet conditions. It prefers the selected session type and matching traction control and ABS settings, and labels any fallback.

Expand **Setup menu** to see each published setting in game menu order, with your recorded value beside the suggested value. Apply the settings in the garage. If a source omits a setting, keep your current value.

Treat this as a starting point: published lap times are self-reported, and a community setup may need adjustment for your car and driving style. The card includes its source and when it was checked.

### 2. Establish a baseline

Warm up, then drive **three clean timed laps** with the same setup and tyre compound, similar fuel, and similar tyre age. Invalid laps, pit laps, damage, incomplete recordings, and other unsuitable laps do not count toward the coaching baseline.

The lap timeline shows which laps qualify. If the coach is still waiting, open the lap details to see why.

### 3. Test one change

After enough comparable laps, the coach checks for repeated input patterns and may propose a small setup experiment. You can also report how the car feels using feedback such as **Won't turn**, **Rear slides**, or **Wheels lock**.

Choose **Start this test**, make the displayed change in the game's garage, and return to the track. Telemetry confirms the new value. Drive another **three comparable clean laps**.

The result compares median pace and the fastest test lap, then suggests keeping the change provisionally, reverting, or collecting more laps. Complete one test before starting another. If the evidence is unclear, give handling feedback and collect more laps.

These suggestions are experiments supported by telemetry cues; an input pattern alone cannot prove understeer or wheelspin. Setup testing is available during live free practice.

## Compare laps and review races

| Screen | Use it for |
| --- | --- |
| **Engineer** | Your next action, setup tests, track focus, and session review |
| **Live** | Current driving inputs, tyres, brakes, and energy |
| **Laps** | Pace over time, tyre trends, and lap details |
| **You vs others** | Your speed, throttle, and brake traces against an opponent |
| **Speed / Steering & gear / Tyres / Brakes & ERS / Corners** | A closer look at an individual lap |
| **Session** | Recorded conditions, assists, and car settings |

In **You vs others**, choose an opponent, your lap, and their lap. **Blue is you; orange is the opponent.** The graphs share distance from the start line. Select a sector to zoom and move the cursor to compare inputs at the same point.

For comparisons between your own laps, select a reference lap in the lap tower and use **vs** to select the comparison lap. On the Speed view, a negative **B − A** time difference means lap B is faster.

After a race, use the session picker at the top to open the saved session. Start with Engineer's review, then explore the lap timeline and comparisons. Partial or missing traces are labelled; the app cannot reconstruct inputs that were never recorded. Time Trial ghosts provide timing comparisons only.

Close the window or choose **Race Engineer → Quit and Save** to save the recording and stop the recorder.

## Optional: Grok explanations

Recording, visual analysis, and calculated coaching work without Grok. For additional written summaries, driving tips, and practice suggestions:

1. Install the **Grok Build CLI** using the [official installation guide](https://docs.x.ai/build/overview).
2. Sign in with `grok login`.
3. Restart Race Engineer and select **Refresh notes** in Engineer.

The app finds `grok` on your PATH or at `~/.grok/bin/grok`. If it is unavailable or a request times out, calculated coaching continues.

Recordings are saved on your Mac. When Grok is enabled, calculated telemetry evidence is sent to Grok to generate explanations. Community setup lookup sends track, car year, conditions, and assist filters to F1Laps; it does not send your lap traces. Cached starting setups remain available offline with their age shown.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| No live data | Same network; telemetry on; port 20777; format 2026. Drive in a session, allow the app through local-network/firewall settings, or try the direct-IP method above. |
| Recorder cannot start | Quit other Race Engineer instances or telemetry recorders using port 20777. |
| No setup experiment yet | Record three complete, comparable clean laps on the current setup. Check lap exclusion reasons and provide handling feedback. |
| Waiting for a setup change | Apply the displayed value in the garage and return to the track so telemetry can confirm it. |
| Opponent graphs are missing | The selected lap must have recorded input data. Older recordings, partial traces, and Time Trial ghosts may not have it. |
| Grok notes are unavailable | Check CLI installation and sign-in, restart the app, then refresh notes. |

## Recordings and development

Choose **Race Engineer → Open Recordings** to open your saved sessions. Files live in `~/Documents/Race Engineer/sessions/`, including lap data, input traces, opponent recordings, and generated coaching. Analysed sessions also include a readable `debrief.md`.

Logs are at `~/Library/Logs/Race Engineer/app.log`. When reporting a problem, include your game version, game platform, session type, and what happened.

To build from source, install the Xcode command-line tools and the uv-managed CPython **3.11.16** Apple Silicon runtime expected by `build.py` (`uv python install 3.11.16`). From this repository, run:

```sh
python3 package.py
```

This builds the app, creates a versioned ZIP in `dist/`, checks ZIP integrity and the extracted app signature, and writes a SHA-256 checksum. Personal recordings, caches, CLI authentication, and logs are not included.

The main components are `App.swift` (native window), `app_backend.py` (recorder lifecycle), `engineer.py` and `f1_udp.py` (telemetry), `dashboard.html` (dashboard), `coaching.py` (coaching), and `setup_baseline.py` (community setups).

## Credits

a fork of my brother John's Race Engineer

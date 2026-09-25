# Race Engineer for macOS

Installed app: `/Applications/Race Engineer.app`. A shortcut is on the Desktop.

Double-click the app to start the dashboard and recorder. No Terminal, uv, separate Python installation, or browser is needed. Closing the window or choosing **Race Engineer → Quit and Save** flushes the recording and stops the backend. **Race Engineer → Open Recordings** opens the saved sessions.

Recordings and editable corner names live in `~/Documents/Race Engineer/`. Existing sessions were copied from `~/Documents/Projects/race-engineer/sessions`; the originals remain there. Keep the old command-line recorder stopped when using this app, because both listen on UDP port 20777.

This build includes the dashboard, You vs others tab, recorder, and Python 3.11 runtime for Apple Silicon. Voice is off, matching the previous launcher. It requires macOS 13 or newer. It is locally signed for this Mac, not notarized for public distribution.

Source: `App.swift` is the native Cocoa/WebKit shell. `app_backend.py` manages recording and graceful shutdown; `bootstrap.py` loads bundled code in Python isolated mode. `build.py` assembles and locally signs the app using the installed uv-managed Python runtime as the build input. Runtime operation does not depend on that external installation.

Validation completed: Swift build, code-sign verification, comparison tests, packet layout checks, bundled-runtime startup, HTTP dashboard, duplicate instance prevention, synthetic UDP recording and flush on parent EOF, and native window displaying actual Baku telemetry.

Logs: `~/Library/Logs/Race Engineer/app.log`.

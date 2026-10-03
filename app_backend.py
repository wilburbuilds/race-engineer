"""Owned by the native app. EOF on stdin means save and exit, even after an app crash."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import sys
import threading


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--udp-port', type=int, default=20777)
    args = parser.parse_args()
    data = Path(args.data_dir)
    data.mkdir(parents=True, exist_ok=True)
    lock = (data / '.recorder.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('Race Engineer is already recording. Quit the other instance first.')
    os.environ['SESSIONS_DIR'] = str(data / 'sessions')
    import engineer
    from f1_udp import RaceState, TelemetryListener
    engineer.CORNER_NAMES_FILE = data / 'corner_names.json'
    state = RaceState()
    logger = engineer.SessionLogger(state)
    listener = TelemetryListener(state, port=args.udp_port)
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    def watch_owner():
        for line in sys.stdin:
            if line.strip() == 'quit':
                break
        stopped.set()
    threading.Thread(target=watch_owner, daemon=True).start()
    dashboard = None
    try:
        listener.start()
        stopped.wait(.35)
        if listener.error:
            raise RuntimeError(listener.error)
        dashboard = engineer.DashboardServer(state, logger, port=0)
        # Port zero lets the OS choose an available private dashboard port.
        dashboard.port = dashboard.httpd.server_address[1]
        print(json.dumps({'url': dashboard.url}), flush=True)
        while not stopped.wait(.5):
            if listener.error:
                raise RuntimeError(listener.error)
    finally:
        listener.stop()
        listener.join(timeout=2)
        folder = logger.flush()
        try:
            logger.coaching.finish(folder)
        except Exception as error:
            print(f"Could not finish coaching report: {error}", file=sys.stderr)
        if dashboard:
            dashboard.httpd.shutdown()
            dashboard.httpd.server_close()

if __name__ == '__main__':
    main()

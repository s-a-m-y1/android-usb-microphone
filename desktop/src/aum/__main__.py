"""Entry points.

  python3 -m aum            -> GTK GUI
  python3 -m aum --record N OUT.wav
                            -> CLI Phase-1 style capture (no GUI)
  python3 -m aum --start-minimized -> GUI minimized
"""

from __future__ import annotations

import argparse
import logging
import sys
import time


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aum-mic",
                                     description="Android USB Microphone")
    parser.add_argument("--start-minimized", action="store_true",
                        help="start without showing the window")
    parser.add_argument("--start-streaming", action="store_true",
                        help="begin streaming immediately at launch")
    parser.add_argument("--screenshot", metavar="FILE.png", default=None,
                        help="debug: render the window to a PNG and exit")
    parser.add_argument("--record", metavar="N", type=int, default=None,
                        help="CLI mode: record N seconds of phone audio")
    parser.add_argument("--out", metavar="FILE.wav", default="test.wav")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    if args.record is not None:
        return _cli_record(args.record, args.out)

    from aum.ui.app import AumApp
    app = AumApp(autostart_stream=args.start_streaming,
                 screenshot_path=args.screenshot)
    return app.run(None)


def _cli_record(seconds: int, out: str) -> int:
    from aum.core.engine import Engine, AppState
    from aum.core.settings import Settings
    from aum.core.daemon import DaemonError

    settings = Settings.load()
    last = {"state": None}

    def on_status(st) -> None:
        if st.state != last["state"]:
            last["state"] = st.state
            print(f"[*] {st.state.name}: {st.message}")
        if st.forward_port:
            pass

    engine = Engine(settings, on_status)
    try:
        engine.ensure_daemon()
    except DaemonError as e:
        print(f"[!] {e}", file=sys.stderr)
        return 2
    engine.start_background()
    engine.start_wav_capture(out)
    engine.start_streaming()

    print(f"[*] Recording {seconds}s from the phone to {out} ...")
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    dur = engine.stop_wav_capture()
    engine.stop_streaming()
    engine.shutdown()
    wrote = last.get("wav_seconds")
    print(f"[*] Wrote {out} ({dur:.1f}s requested)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

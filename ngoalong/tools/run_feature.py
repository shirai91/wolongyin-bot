"""Run one feature on the live game through the app's own code path (``AppServices`` →
``TaskRunner`` → the feature's ``run``), without the window (docs/08, phase R2 live tests).

    python -m ngoalong.tools.run_feature quay_ruong --set opens=10
    python -m ngoalong.tools.run_feature thap_thi_luyen --set mode=sweep --set start_floor=21 --set sweep_runs=5
    python -m ngoalong.tools.run_feature quay_ruong --stop-after 12      # press Stop after 12 s, report the latency
    python -m ngoalong.tools.run_feature thap_thi_luyen --debug          # annotated PNG per tick in the run folder
    python -m ngoalong.tools.run_feature cong_trinh --gold yes           # answer "skip the wait with Vàng" yes for this run

Params start from the ones saved in ``settings.toml`` (the "Tính năng" form) and ``--set`` overrides
them for this run only; nothing is saved. The run log lines are printed as they come; the run
folder (``logs/run-…``) holds ``run.log`` and the PNGs. Ctrl+C presses Stop.
"""

from __future__ import annotations

import argparse
import sys
import time

from PySide6.QtCore import QCoreApplication, Qt

from ngoalong.config.settings import DEFAULT_SETTINGS_PATH, load_or_import
from ngoalong.engine import registry


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ngoalong.tools.run_feature", description=__doc__.split("\n\n")[0])
    ap.add_argument("feature", help="feature id (quay_ruong, thap_thi_luyen, ...)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="param override")
    ap.add_argument("--settings", default=str(DEFAULT_SETTINGS_PATH))
    ap.add_argument("--window", type=int, default=0, help="index of the game window when several are open")
    ap.add_argument("--debug", action="store_true", help="debug mode: an annotated PNG for every tick")
    ap.add_argument("--stop-after", type=float, default=0.0, help="press Stop after this many seconds")
    ap.add_argument("--gold", choices=["yes", "no"], default=None,
                    help="Cài đặt 'Dùng vàng bỏ qua thời gian chờ' for this run only (default: as saved)")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

    from ngoalong.app import AppServices

    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])  # noqa: F841 - Qt objects need it
    features = registry.discover()
    settings, _ = load_or_import(args.settings, None, features)
    if args.gold is not None:
        settings.game.spend_gold_skip_wait = args.gold == "yes"
    services = AppServices(settings, settings_path=None)  # never saves
    cls = registry.get_feature(args.feature)
    raw = settings.feature_params(cls).model_dump()
    for item in args.set:
        key, _, value = item.partition("=")
        if key not in cls.Params.model_fields:
            raise SystemExit(f"{cls.id} has no param {key!r} (has {sorted(cls.Params.model_fields)})")
        raw[key] = value
    params = cls.Params.model_validate(raw)
    windows = services.discover_windows()
    if args.window >= len(windows):
        raise SystemExit("no Coowon 'Ngọa Long' window found")
    services.bind(windows[args.window])
    if args.debug:
        services.debug.set_enabled(True)

    t0 = time.monotonic()

    def show(level: str, message: str) -> None:
        print(f"{time.monotonic() - t0:7.1f} [{level}] {message}", flush=True)

    runner = services.runner
    runner.log.connect(show, Qt.ConnectionType.DirectConnection)
    print(f"{cls.id}: {params.model_dump(mode='json')}")
    hooks = runner.start(cls(params).bind_services(services), services.make_actor)
    stop_at: float | None = None
    try:
        while not runner.wait(0.05):
            if args.stop_after and stop_at is None and time.monotonic() - t0 >= args.stop_after:
                stop_at = time.monotonic()
                print("--- Stop", flush=True)
                runner.stop()
    except KeyboardInterrupt:
        stop_at = time.monotonic()
        runner.stop()
        runner.wait(5.0)
    ended = time.monotonic()
    r = runner.last_result
    print(f"--- {r.status.value if r else '?'}: {r.message if r else ''} ({ended - t0:.1f} s) run folder: {hooks.run_dir}")
    if r is not None and r.counters:
        print(f"--- counters: {r.counters}")
    if stop_at is not None:
        print(f"--- Stop latency: {ended - stop_at:.2f} s")
    services.shutdown()
    return 0 if r is not None and r.status.value != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

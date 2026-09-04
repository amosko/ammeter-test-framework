"""Start the ammeter emulators listed in the config and read one measurement from each."""

import argparse
import sys
import threading
import time
from pathlib import Path

from Ammeters.base_ammeter import AmmeterEmulatorBase
from Ammeters.Circutor_Ammeter import CircutorAmmeter
from Ammeters.client import AmmeterError, is_listening, read_current, wait_for_ammeter
from Ammeters.Entes_Ammeter import EntesAmmeter
from Ammeters.Greenlee_Ammeter import GreenleeAmmeter
from src.utils.config import DEFAULT_CONFIG_PATH, AmmeterSpec, Config, ConfigError

EMULATORS: dict[str, type[AmmeterEmulatorBase]] = {
    "greenlee": GreenleeAmmeter,
    "entes": EntesAmmeter,
    "circutor": CircutorAmmeter,
}


def start_emulators(config: Config) -> list[AmmeterSpec]:
    """Start an emulator thread for every configured ammeter that has one; returns the specs served."""
    specs = [spec for name, spec in config.ammeters.items() if name in EMULATORS]
    if not specs:
        raise AmmeterError(f"none of the configured ammeters has an emulator (known: {', '.join(EMULATORS)})")
    for spec in specs:
        if is_listening(spec.host, spec.port):
            raise AmmeterError(f"port {spec.port} is already in use; is another main.py running?")
        threading.Thread(target=EMULATORS[spec.name](spec.port).start_server, daemon=True).start()
        wait_for_ammeter(spec.host, spec.port)
    return specs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="config file (default: %(default)s)")
    args = parser.parse_args()
    try:
        for spec in start_emulators(Config.load(args.config)):
            current = read_current(spec.host, spec.port, spec.command, spec.timeout_s)
            print(f"{spec.name} (port {spec.port}): {current} A")
        print("Emulators are running. Press Ctrl+C to stop.")
        while True:
            time.sleep(1)
    except (ConfigError, AmmeterError) as exc:
        sys.exit(f"error: {exc}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

"""Start the ammeter emulators listed in the config and read one measurement from each."""
import argparse
import sys
import threading
import time
from pathlib import Path

from Ammeters.base_ammeter import AmmeterEmulatorBase
from Ammeters.Circutor_Ammeter import CircutorAmmeter
from Ammeters.client import AmmeterError, request_current_from_ammeter, wait_for_ammeter
from Ammeters.Entes_Ammeter import EntesAmmeter
from Ammeters.Greenlee_Ammeter import GreenleeAmmeter
from src.utils.config import DEFAULT_CONFIG_PATH, Config, ConfigError

EMULATORS: dict[str, type[AmmeterEmulatorBase]] = {
    "greenlee": GreenleeAmmeter,
    "entes": EntesAmmeter,
    "circutor": CircutorAmmeter,
}


def start_emulators(config: Config) -> list[AmmeterEmulatorBase]:
    """Start every configured emulator on its port in a daemon thread and wait until it accepts connections."""
    emulators = [EMULATORS[name](spec.port) for name, spec in config.ammeters.items() if name in EMULATORS]
    for emulator in emulators:
        thread = threading.Thread(target=emulator.start_server, daemon=True)
        thread.start()
        wait_for_ammeter("localhost", emulator.port)
        if not thread.is_alive():
            raise AmmeterError(f"{type(emulator).__name__} failed to start; is port {emulator.port} already in use?")
    return emulators


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="config file (default: %(default)s)")
    args = parser.parse_args()

    try:
        emulators = start_emulators(Config.load(args.config))
        for emulator in emulators:
            request_current_from_ammeter(emulator.port, emulator.get_current_command)
    except (ConfigError, AmmeterError) as exc:
        sys.exit(f"error: {exc}")

    print("Emulators are running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

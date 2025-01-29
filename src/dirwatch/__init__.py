import argparse
import fnmatch
import logging
import os
import re
import signal
import subprocess
import sys
import threading
from collections.abc import Callable

import fswatch.libfswatch


class UserError(Exception):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "-d",
        "--directory",
        default=".",
        help="The directory to watch, defaults to the current directory.",
    )

    parser.add_argument(
        "-i",
        "--include",
        action="append",
        help="Include only paths matching the specified pattern. Can be "
        "specified multiple times to match more files. Defaults to `*`, "
        "unless this option is used at least once",
    )

    parser.add_argument(
        "-e",
        "--exclude",
        action="append",
        help="Exclude paths matching the specified pattern. Can be specified "
        "multiple times to exclude more files. Also applies to files "
        "included with --include. Defaults to `.*`, unless this option "
        "is used at least once.",
    )

    parser.add_argument(
        "-w",
        "--watch",
        action="store_true",
        help="Behave similar to procps-ng watch. Clear console before running "
        "the command and tolerate errors of the called command. Also "
        "reports the exist status in every case.",
    )

    parser.add_argument(
        "-k",
        "--kill",
        action="store_true",
        help="Kill and restart the command when it is still running when "
        "additional changes are detected.",
    )

    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print the path associated with each detected change.",
    )

    parser.add_argument("-q", "--quiet", action="store_true", help="Disable logging.")

    parser.add_argument(
        "command",
        nargs="...",
        help="Command which is executed whenever a change in the watched "
        "directory is detected.",
    )

    return parser.parse_args()


class Manager:
    def __init__(self, *, command: list[str], watch: bool, kill: bool):
        self._watch = watch
        self._command = command
        self._kill = kill

        self._current_process: subprocess.Popen[bytes] | None = None
        self._process_terminated = False
        self._pending_modification = False

    def handle_modification(self) -> None:
        self._pending_modification = True
        self._check_pending_modification()

    def handle_sigchld(self) -> None:
        logging.debug(f"Received SIGCHLD.")

        if self._current_process is not None:
            self._reap_process()

    def handle_exit(self) -> None:
        if self._current_process is not None:
            self._terminate_process()
            self._current_process.wait()

    def _check_pending_modification(self) -> None:
        if self._pending_modification:
            if self._current_process is None:
                self._pending_modification = False
                self._process_terminated = False
                self._start_process()
            elif self._kill and not self._process_terminated:
                self._process_terminated = True
                self._terminate_process()

    def _start_process(self) -> None:
        assert self._current_process is None

        if self._watch:
            print("\x1bc", end="", flush=True)

        logging.debug(f'Starting command: {" ".join(self._command)}')

        self._current_process = subprocess.Popen(self._command, start_new_session=True)

    def _reap_process(self) -> None:
        assert self._current_process is not None

        try:
            self._current_process.wait(0)
        except subprocess.TimeoutExpired:
            pass
        else:
            returncode = self._current_process.returncode
            level = logging.INFO if self._watch else logging.DEBUG

            if returncode:
                logging.log(level, f"Command failed with exit code {returncode}.")
            else:
                logging.log(level, "Command completed successfully.")

            self._current_process = None
            self._check_pending_modification()

    def _terminate_process(self) -> None:
        assert self._current_process is not None

        logging.info("Sending SIGTERM to process group.")

        try:
            os.killpg(self._current_process.pid, signal.SIGTERM)
        except PermissionError:
            # Happens when the process has already exited.
            pass


class _Monitor(fswatch.Monitor):  # type: ignore
    # Workaround for https://github.com/paul-nameless/pyfswatch/issues/4.
    def start(self) -> None:
        fswatch.libfswatch.fsw_start_monitor(self.handle)


def start_monitor(
    directory: str,
    include: list[str],
    exclude: list[str],
    on_modification: Callable[[], None],
) -> None:
    include_re = "|".join(fnmatch.translate(i) for i in include)
    exclude_re = "|".join(fnmatch.translate(i) for i in exclude)

    def callback(
        path: bytes, evt_time: int, flags: int, flags_num: int, event_num: int
    ) -> None:
        path_str = os.path.relpath(path.decode(), directory)

        # It would be technically possible to pass the include pattern to
        # fswatch and only evaluate the exclude pattern here (or vice-versa),
        # but `fnmatch.translate()` makes use of multiple non-POSIX features
        # without a good replacement in C++'s regex implementation. In the end
        # I just couldn't be bothered.
        if re.match(include_re, path_str) and not re.match(exclude_re, path_str):
            logging.debug(f"Changed: {path_str}")
            on_modification()

    monitor = _Monitor()
    monitor.add_path(directory)
    monitor.set_recursive()
    monitor.set_callback(callback)

    threading.Thread(target=monitor.start, daemon=True).start()


def main(
    directory: str,
    include: list[str] | None,
    exclude: list[str] | None,
    command: list[str],
    watch: bool,
    kill: bool,
    debug: bool,
    quiet: bool,
) -> None:
    if debug:
        logging.root.setLevel(logging.DEBUG)

    if quiet:
        logging.root.setLevel(logging.ERROR)

    if include is None:
        include = ["*"]

    if exclude is None:
        exclude = [".*"]

    manager = Manager(command=command, watch=watch, kill=kill)
    event = threading.Event()

    signal.signal(signal.SIGCHLD, lambda signal, frame: manager.handle_sigchld())
    signal.signal(signal.SIGTERM, signal.default_int_handler)

    start_monitor(directory, include, exclude, event.set)

    try:
        while True:
            # Start the process once initially, even without any events.
            manager.handle_modification()

            event.wait()
            event.clear()
    except KeyboardInterrupt:
        manager.handle_exit()


def entry_point() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        main(**vars(parse_args()))
    except UserError as e:
        logging.error(f"error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logging.error("Operation interrupted.")
        sys.exit(130)

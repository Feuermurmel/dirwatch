#! /usr/bin/env python3

import os
import sys
import signal
from subprocess import Popen, TimeoutExpired
from argparse import ArgumentParser
from fnmatch import fnmatchcase
from queue import Queue

from os.path import normpath
from watchdog.events import DirModifiedEvent, FileSystemEventHandler
from watchdog.observers import Observer


class UserError(Exception):
    pass


def log(msg, *args):
    print(
        '{}: {}'.format(os.path.basename(sys.argv[0]), msg.format(*args)),
        file=sys.stderr,
        flush=True)


def parse_args():
    parser = ArgumentParser()

    parser.add_argument(
        '-d',
        '--directory',
        default='.',
        help='The directory to watch, defaults to the current directory.')

    parser.add_argument(
        '-i',
        '--include',
        action='append',
        help='Include only paths matching the specified pattern. Can be '
             'specified multiple times to match more files.')

    parser.add_argument(
        '-e',
        '--exclude',
        action='append',
        help='Exclude paths matching the specified pattern. Can be specified '
             'multiple times to exclude more files. Also applies to files '
             'included with --include.')

    parser.add_argument(
        '-w',
        '--watch',
        action='store_true',
        help='Behave similar to procps-ng watch. Clear console before running '
             'the command and tolerate errors of the called command. Also '
             'reports the exist status in any case.')

    parser.add_argument(
        '-k',
        '--kill',
        action='store_true',
        help='Kill and restart the command when it is still running when '
             'additional changes are detected.')

    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug mode. This will print all events and whether it '
             'was ignored or not according to the specified settings.'
    )

    parser.add_argument('command', nargs='...')

    return parser.parse_args()


class Manager:
    def __init__(self, *, command, watch):
        self._watch = watch
        self._command = command

        self._current_process = None
        self._pending_modification = False

    def handle_modification(self):
        self._pending_modification = True
        self._check_pending_modification()

    def handle_sigchld(self):
        if self._current_process is not None:
            self._reap_process()

    def handle_exit(self):
        if self._current_process is not None:
            self._terminate_process()
            self._current_process.wait()

    def _check_pending_modification(self):
        if self._pending_modification:
            if self._current_process is None:
                self._pending_modification = False
                self._start_process()

    def _start_process(self):
        assert self._current_process is None

        if self._watch:
            print('\x1bc', end='', flush=True)

        self._current_process = Popen(self._command, start_new_session=True)

    def _reap_process(self):
        try:
            self._current_process.wait(0)
        except TimeoutExpired:
            pass
        else:
            if self._watch:
                returncode = self._current_process.returncode

                if returncode:
                    log('Command failed with exit code {}.'.format(returncode))
                else:
                    log('Command completed successfully.')

            self._current_process = None
            self._check_pending_modification()

    def _terminate_process(self):
        log('Sending SIGTERM to process group.')

        try:
            os.killpg(self._current_process.pid, signal.SIGTERM)
        except PermissionError:
            # Happens when the process has already exited.
            pass


def start_observer(directory, include, exclude, debug, on_modification):
    def path_matches(path):
        # A path must be matched by any include pattern and by none of the
        # exclude patterns.
        return any(fnmatchcase(path, i) for i in include) \
               and not any(fnmatchcase(path, i) for i in exclude)

    def event_matches(event):
        if isinstance(event, DirModifiedEvent):
            return False
        else:
            # One or both may be None.
            paths = [
                event.src_path,
                getattr(event, 'dest_path', None)]

            return any(
                i is not None and path_matches(normpath(i))
                for i in paths)

    class EventHandler(FileSystemEventHandler):
        def on_any_event(self, event):
            matches = event_matches(event)

            if debug:
                log('{}: {}', event, 'Matched' if matches else 'Ignored')

            if matches:
                on_modification()

    event_handler = EventHandler()

    observer = Observer()
    observer.schedule(event_handler, directory, recursive=True)

    observer.start()


def main(directory, include, exclude, command, watch, debug):
    if include is None:
        include = ['*']

    if exclude is None:
        exclude = ['.*']

    manager = Manager(command=command, watch=watch)
    event_queue = Queue()

    signal.signal(signal.SIGCHLD, lambda signal, frame: manager.handle_sigchld())
    signal.signal(signal.SIGTERM, signal.default_int_handler)

    start_observer(
        directory,
        include,
        exclude,
        debug,
        lambda: event_queue.put(manager.handle_modification))

    try:
        # Start the process once initially, even without any events.
        manager.handle_modification()

        while True:
            event_queue.get()()
    except KeyboardInterrupt:
        manager.handle_exit()


def entry_point():
    try:
        main(**vars(parse_args()))
    except UserError as e:
        log('Error: {}', e)
        sys.exit(1)
    except KeyboardInterrupt:
        log('Operation interrupted.')
        sys.exit(2)

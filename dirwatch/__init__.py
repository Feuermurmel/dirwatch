#! /usr/bin/env python3

import os
import sys
from argparse import ArgumentParser
from fnmatch import fnmatchcase
from subprocess import Popen
from threading import Event

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


def exec(args):
    process = Popen(args)
    process.wait()

    if process.returncode:
        raise UserError(
            'Command failed with exit code {}.'.format(process.returncode))


def parse_args():
    parser = ArgumentParser()

    parser.add_argument(
        '-d',
        '--directory',
        default='.',
        help='The directory to watch, defaults too the current directory.')

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
        '--debug',
        action='store_true',
        help='Enable debug mode. This will print all events and whether it '
             'was ignored or not according to the specified settings.'
    )

    parser.add_argument('command', nargs='...')

    return parser.parse_args()


def main(directory, include, exclude, command, watch, debug):
    if include is None:
        include = ['*']

    if exclude is None:
        exclude = ['.*']

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

    change_event = Event()

    class EventHandler(FileSystemEventHandler):
        def on_any_event(self, event):
            matches = event_matches(event)

            if debug:
                log('{}: {}', event, 'Matched' if matches else 'Ignored')

            if matches:
                change_event.set()

    event_handler = EventHandler()

    observer = Observer()
    observer.schedule(event_handler, directory, recursive=True)
    observer.start()

    while True:
        if watch:
            print('\x1bc', end='', flush=True)

            try:
                exec(command)
            except UserError as e:
                log('{}', e)
            else:
                log('Command completed successfully.')
        else:
            exec(command)

        change_event.wait()
        change_event.clear()


def entry_point():
    try:
        main(**vars(parse_args()))
    except UserError as e:
        log('Error: {}', e)
        sys.exit(1)
    except KeyboardInterrupt:
        log('Operation interrupted.')
        sys.exit(2)

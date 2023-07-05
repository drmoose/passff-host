#!/usr/bin/env python3

import atexit
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import namedtuple
import json
import os
from os.path import join, dirname, basename
from functools import cached_property, wraps
from itertools import zip_longest
from pprint import pprint
import re
import shutil
import shlex
import subprocess
import tempfile
import sys

OCI_PROGRAM = (('podman' if shutil.which('podman') else 'docker'),)
if os.environ.get('OCI_REQUIRES_SUDO'):
    OCI_PROGRAM = ('sudo',) + OCI_PROGRAM
HERE = dirname(__file__)

# Sed expressions for tweaking the behavior of passff.py for various
BASE_CASE = '/status-fd/d'  # before I started trying to capture status...
UNMODIFIED = ''
NO_STATUS = "s/'status-fd.*2',//"  # debug on, status-fd off
NO_DEBUG = "s/'debug.*'//"

TEST_LANGS = {
    'es_ES.UTF-8': ':es:',
    'ja_JP.UTF-8': ':jp:',
    'en_US.UTF-8': ':us:',
}
DEFAULT_LANG = 'en_US.UTF-8'

Interesting = namedtuple(
    'Interesting', ('human', 'pinmode', 'entry', 'is_error', 'test')
)

INTERESTING_CONDITIONS = [
    Interesting(
        'Found',
        'loopback',
        'test',
        False,
        lambda x: 'hello world' in x['stdout'],
    ),
    Interesting(
        'Cancel (99)', 'cancel', 'test', True, lambda x: x['errorCode'] == 99
    ),
    Interesting(
        'Bad Pin (11)', 'wrong', 'test', True, lambda x: x['errorCode'] == 11
    ),
    Interesting(
        'No Pinentry (85)',
        'error',
        'test',
        True,
        lambda x: x['errorCode'] == 85,
    ),
    Interesting(
        'No Secret (17)',
        'loopback',
        'unreadable',
        True,
        lambda x: x['errorCode'] == 17,
    ),
]


# Rebuilding these OCI containers every time has proven to be enough of a testing delay, even with the podman cache, that I don't want to do it anymore.
try:
    OCI_CACHE_FILE = open('test_cache.json', 'r+')
    OCI_CACHE = json.load(OCI_CACHE_FILE)
except:
    OCI_CACHE_FILE = open('test_cache.json', 'w')
    OCI_CACHE = {}
SAVED_OUTCOMES = []


def _persist_oci_cache(fn):
    @wraps(fn)
    def decorator(self, *a, **kw):
        out = fn(self, *a, **kw)
        self.cache_entry[fn.__name__] = out
        return out

    return decorator


@atexit.register
def write_cache():
    OCI_CACHE_FILE.seek(0)
    json.dump(OCI_CACHE, OCI_CACHE_FILE)
    OCI_CACHE_FILE.truncate()


def dot_progress(stream, title):
    print(title, end='')
    for x in stream:
        yield x
        print('.', end='')
        sys.stdout.flush()
    print()


def parse_version_tuple(text):
    return tuple(int(x) for x in re.findall(r'\d+', text))


class GpgOCIEnvironment:
    def __init__(self, base_image):
        self.base_image = base_image
        self.name = basename(base_image)
        self.iid = None
        self.cache_entry = OCI_CACHE.get(self.name, {})
        for k, v in self.cache_entry.items():
            setattr(self, k, v)
        if self.iid:
            return
        with tempfile.TemporaryDirectory() as td:
            iidfile = join(td, 'iid.txt')
            subprocess.check_call(
                OCI_PROGRAM
                + (
                    'build',
                    '--iidfile=%s' % iidfile,
                    '--build-arg=FROM=%s' % base_image,
                    'docker',
                )
            )
            with open(iidfile) as fd:
                self.iid = fd.read().strip()
                self.cache_entry['iid'] = self.iid
            assert self.has_working_locales, base_image
            OCI_CACHE[self.name] = self.cache_entry

    def run(self, *args, opts='', lang='en_US.UTF-8', **kw):
        language = lang.partition('.')[0].partition('_')[0]
        action = '"$@"'
        if not kw.pop('check', True):
            action += ' || true'
        return subprocess.check_output(
            OCI_PROGRAM
            + (
                'run',
                '--rm',
                '-iv',
                '%s/src:/src:ro,Z' % HERE,
                '-e',
                'LC_ALL=' + lang,
                '-e',
                'LANG=' + lang,
                '-e',
                'LANGUAGE=' + lang,
                '-e',
                'PASSWORD_STORE_GPG_OPTS=' + opts,
                self.iid,
                'bash',
                '-ec',
                'locale | sed "s:^:export :" > /tmp/lc; . /tmp/lc; ' + action,
                '--',
            )
            + args,
            **kw,
        )

    @cached_property
    @_persist_oci_cache
    def gpg_version_string(self):
        return self.run('gpg', '--version', text=True)

    @cached_property
    def gpg_version(self):
        return parse_version_tuple(
            self.gpg_version_string.split('\n')[0].strip().split()[-1]
        )

    @cached_property
    def libgcrypt_version(self):
        return parse_version_tuple(
            self.gpg_version_string.split('\n')[1].strip().split()[-1]
        )

    @cached_property
    @_persist_oci_cache
    def pass_version_string(self):
        return self.run('pass', 'version', text=True)

    @cached_property
    def os_release(self):
        return self.run('cat', '/etc/os-release', text=True)

    @cached_property
    @_persist_oci_cache
    def has_working_spanish(self):
        return 'Ejemplos:' in self.run(
            'gpg', '--help', lang='es_ES.UTF-8', text=True
        )

    @cached_property
    @_persist_oci_cache
    def has_working_japanese(self):
        return '例:' in self.run('gpg', '--help', lang='ja_JP.UTF-8', text=True)

    @property
    def has_working_locales(self):
        return self.has_working_spanish and self.has_working_japanese

    @cached_property
    @_persist_oci_cache
    def baseline_stderrs(self):
        return {
            lang: {
                i.pinmode: o['stderr']
                for i, o in self.try_interesting_things(BASE_CASE, lang)
                if i.is_error
            }
            for lang in TEST_LANGS
        }

    def call_passff(self, pinmode, message, **kw):
        return self.call_hacked_passff(UNMODIFIED, pinmode, message)

    def call_hacked_passff(self, hack, pinmode, message, **kw):
        encoded = b'\xFF\xFF\xFF\xFF' + json.dumps(message).encode()
        payload = self.run(
            'bash',
            '-c',
            'pinmode %s python3 <(sed %s /src/passff.py)'
            % (
                shlex.quote(pinmode),
                shlex.quote('s/decoded_stderr/VERSION/;' + hack),
            ),
            input=encoded,
            **kw,
        )[4:].decode()
        return json.loads(payload)

    def try_interesting_things(self, hack=UNMODIFIED, lang=DEFAULT_LANG):
        task = '%s %s' % (self.name, lang)
        for thing in dot_progress(INTERESTING_CONDITIONS, task):
            yield thing, self.try_interesting_thing(thing, hack, lang)

    def try_interesting_thing(self, thing, hack=UNMODIFIED, lang=DEFAULT_LANG):
        output = self.call_hacked_passff(
            hack,
            thing.pinmode,
            [thing.entry],
            lang=lang,
            check=not thing.is_error,
        )
        SAVED_OUTCOMES.append((lang, self.name, thing.human, hack, output))
        return output


CONTAINER_ENVIRONMENTS = [
    # All the redhats still in support...
    (FC38 := GpgOCIEnvironment('docker.io/library/fedora:38')),
    (FC37 := GpgOCIEnvironment('docker.io/library/fedora:37')),
    (RH9 := GpgOCIEnvironment('docker.io/library/rockylinux:9')),
    (RH8 := GpgOCIEnvironment('docker.io/library/rockylinux:8')),
    (RH7 := GpgOCIEnvironment('docker.io/library/centos:7')),
    # Ubuntus...
    (UBUNTU := GpgOCIEnvironment('docker.io/library/ubuntu')),
    (UR := GpgOCIEnvironment('docker.io/library/ubuntu:rolling')),
    (UJ := GpgOCIEnvironment('docker.io/library/ubuntu:jammy')),
    (UF := GpgOCIEnvironment('docker.io/library/ubuntu:focal')),
    (UB := GpgOCIEnvironment('docker.io/library/ubuntu:bionic')),
    # Debians...
    (SID := GpgOCIEnvironment('docker.io/library/debian:sid')),
    (D12 := GpgOCIEnvironment('docker.io/library/debian:12')),
    (D11 := GpgOCIEnvironment('docker.io/library/debian:11')),
    (D10 := GpgOCIEnvironment('docker.io/library/debian:10')),
]

print()
print("--- All docker containers built. Woohoo. ---")
print()

# A list of gpg/libgcrypt versions still in support
# Based on the EOL announcements at https://gnupg.org/download/index.html
# Make sure I'm covering all of them without building stuff from source...
MISSING_GPG_VERSIONS = {(2, 2), (2, 4)}
MISSING_LIBGCRYPT_VERSIONS = {(1, 8), (1, 9), (1, 10)}


def startswith(haystack, needle):
    return haystack[: len(needle)] == needle


def not_missing(collection, version):
    for entry in collection:
        if startswith(version, entry):
            collection.remove(entry)
            break


def compose_markdown_row(row_tpl, col_count, row):
    return row_tpl % tuple(
        str(r or '') for r, _ in zip_longest(row, range(col_count))
    )


def render_markdown_table(columns, data):
    lens = [len(x) for x in columns]
    for row in data:
        for ii, x in enumerate(row):
            if ii == len(lens):
                lens.append(0)
            lens[ii] = max(lens[ii], len(str(x)))
    row_tpl = '|' + ''.join(f' %-{x}s |' for x in lens)
    print(compose_markdown_row(row_tpl, len(lens), columns))
    print(compose_markdown_row(row_tpl, len(lens), []).replace(' ', '-'))
    for row in data:
        print(compose_markdown_row(row_tpl, len(lens), row))


# Print out a "who provides what" table for my own sanity, since it took me 4
# hours to get this far.
info_cols = []
info_data = []


def _make_lambda(collection, index, fstring):
    return lambda x: collection.__setitem__(index, fstring % x)


def column_helper(name, fstring='%s'):
    task = f'populating {name} column'
    col = len(info_cols)
    info_cols.append(name)
    for ii, distro in dot_progress(enumerate(CONTAINER_ENVIRONMENTS), task):
        if ii == len(info_data):
            info_data.append(row := [])
        else:
            row = info_data[ii]
        while len(row) <= col:
            row.append("")
        yield distro, _make_lambda(row, col, fstring)


for distro, value in column_helper('distro', '`%s`'):
    value(distro.name)


for distro, value in column_helper('gpg version'):
    not_missing(MISSING_GPG_VERSIONS, distro.gpg_version)
    value('.'.join(str(s) for s in distro.gpg_version))


for distro, value in column_helper('libgcrypt'):
    not_missing(MISSING_LIBGCRYPT_VERSIONS, distro.libgcrypt_version)
    value('.'.join(str(s) for s in distro.libgcrypt_version))


print()
render_markdown_table(info_cols, info_data)


print()
print("checking for missing version coverage")
assert not MISSING_GPG_VERSIONS, repr(MISSING_GPG_VERSIONS)
assert not MISSING_LIBGCRYPT_VERSIONS, repr(MISSING_LIBGCRYPT_VERSIONS)
print("ok")
print()


def run_test_case(thing, distro, value):
    flags = set()
    for lang, baseline_stderrs in distro.baseline_stderrs.items():
        baseline = baseline_stderrs[thing.pinmode].count('\n')
        outcome = distro.try_interesting_thing(thing, lang=lang)
        if outcome['stderr'].count('\n') > baseline and thing.is_error:
            flags.add(':warning:')
        if thing.test(outcome):
            flags.add(TEST_LANGS[lang])
        elif outcome['errorCode']:
            flags.add(':x:')
            flags.add(str(outcome['errorCode']))
    # see what happens without the debug flag
    outcome = distro.try_interesting_thing(thing, hack=NO_DEBUG)
    if flags & set(TEST_LANGS.values()) and not thing.test(outcome):
        flags.add(':bug:')
    value(' '.join(sorted(flags)))


with ThreadPoolExecutor() as exe:
    queue = [
        exe.submit(run_test_case, thing, distro, value)
        for thing in INTERESTING_CONDITIONS
        for distro, value in column_helper(thing.human)
    ]
    for _ in dot_progress(as_completed(queue), 'waiting for threads'):
        pass

SAVED_OUTCOMES.sort()
with open('test_results_detail.json', 'w') as fd:
    json.dump(SAVED_OUTCOMES, fd)

print()
render_markdown_table(info_cols, info_data)

print()
print("Legend:")
print("- :us: Works in Engilsh")
print("- :es: Works in Spanish")
print("- :jp: Works in Japanese")
print("- :bug: Only works with `--debug=ipc`")
print("- :warning: Substantially different stderr output")
print("- :x: Detected as something else")

#!/usr/bin/env python

# NOTE: This script requires python 3.

"""Script to generate Chromium's Abseil .def files at roll time.

This script generates //third_party/abseil-app/absl/symbols_*.def at Abseil
roll time.

Since Abseil doesn't export symbols, Chromium is forced to consider all
Abseil's symbols as publicly visible. On POSIX it is possible to use
-fvisibility=default but on Windows a .def file with all the symbols
is needed.

Unless you are on a Windows machine, you need to set up your Chromium
checkout for cross-compilation by following the instructions at
https://chromium.googlesource.com/chromium/src.git/+/master/docs/win_cross.md.
If you are on Windows, you may need to tweak this script to run, e.g. by
changing "gn" to "gn.bat", changing "llvm-nm-9" to the name of your copy of
llvm-nm, etc.
"""

import fnmatch
import logging
import os
import re
import subprocess
import sys
import tempfile
import time

# Matches a mangled symbol that has 'absl' in it, this should be a good
# enough heuristic to select Abseil symbols to list in the .def file.
ABSL_SYM_RE = re.compile(r'0* [BT] (?P<symbol>(\?+)[^\?].*absl.*)')
if sys.platform == 'win32':
  # Typical dumpbin /symbol lines look like this:
  # 04B 0000000C SECT14 notype       Static       | ?$S1@?1??SetCurrent
  # ThreadIdentity@base_internal@absl@@YAXPAUThreadIdentity@12@P6AXPAX@Z@Z@4IA
  #  (unsigned int `void __cdecl absl::base_internal::SetCurrentThreadIdentity...
  # We need to start on "| ?" and end on the first " (" (stopping on space would
  # also work).
  # This regex is identical inside the () characters except for the ? after .*,
  # which is needed to prevent greedily grabbing the undecorated version of the
  # symbols.
  ABSL_SYM_RE = '.*External     \| (?P<symbol>(\?+)[^\?].*?absl.*?) \(.*'
  # Typical exported symbols in dumpbin /directives look like:
  #    /EXPORT:?kHexChar@numbers_internal@absl@@3QBDB,DATA
  ABSL_EXPORTED_RE = '.*/EXPORT:(.*),.*'


def _DebugOrRelease(is_debug):
  return 'dbg' if is_debug else 'rel'


def _GenerateDefFile(cpu, is_debug, extra_gn_args=[], suffix=None):
  """Generates a .def file for the absl component build on the specified CPU."""
  if extra_gn_args:
    assert suffix != None, 'suffix is needed when extra_gn_args is used'

  flavor = _DebugOrRelease(is_debug)
  gn_args = [
      'ffmpeg_branding = "Chrome"',
      'is_component_build = true',
      'is_debug = {}'.format(str(is_debug).lower()),
      'proprietary_codecs = true',
      'symbol_level = 0',
      'target_cpu = "{}"'.format(cpu),
      'target_os = "win"',
  ]
  gn_args.extend(extra_gn_args)

  gn = 'gn'
  autoninja = 'autoninja'
  symbol_dumper = ['llvm-nm-9']
  if sys.platform == 'win32':
    gn = 'gn.bat'
    autoninja = 'autoninja.bat'
    symbol_dumper = ['dumpbin', '/symbols']
  with tempfile.TemporaryDirectory() as out_dir:
    logging.info('[%s - %s] Creating tmp out dir in %s', cpu, flavor, out_dir)
    subprocess.check_call([gn, 'gen', out_dir, '--args=' + ' '.join(gn_args)],
                          cwd=os.getcwd())
    logging.info('[%s - %s] gn gen completed', cpu, flavor)
    subprocess.check_call(
        [autoninja, '-C', out_dir, 'third_party/abseil-cpp:absl_component_deps'],
        cwd=os.getcwd())
    logging.info('[%s - %s] autoninja completed', cpu, flavor)

    obj_files = []
    for root, _dirnames, filenames in os.walk(
        os.path.join(out_dir, 'obj', 'third_party', 'abseil-cpp')):
      matched_files = fnmatch.filter(filenames, '*.obj')
      obj_files.extend((os.path.join(root, f) for f in matched_files))

    logging.info('[%s - %s] Found %d object files.', cpu, flavor, len(obj_files))

    absl_symbols = set()
    dll_exports = set()
    if sys.platform == 'win32':
      for f in obj_files:
        # Track all of the functions exported with __declspec(dllexport) and
        # don't list them in the .def file - double-exports are not allowed. The
        # error is "lld-link: error: duplicate /export option".
        exports_out = subprocess.check_output(['dumpbin', '/directives', f], cwd=os.getcwd())
        for line in exports_out.splitlines():
          line = line.decode('utf-8')
          match = re.match(ABSL_EXPORTED_RE, line)
          if match:
            dll_exports.add(match.groups()[0])
    for f in obj_files:
      stdout = subprocess.check_output(symbol_dumper + [f], cwd=os.getcwd())
      for line in stdout.splitlines():
        try:
          line = line.decode('utf-8')
        except UnicodeDecodeError:
          # Due to a dumpbin bug there are sometimes invalid utf-8 characters in
          # the output. This only happens on an unimportant line so it can
          # safely and silently be skipped.
          # https://developercommunity.visualstudio.com/content/problem/1091330/dumpbin-symbols-produces-randomly-wrong-output-on.html
          continue
        match = re.match(ABSL_SYM_RE, line)
        if match:
          symbol = match.group('symbol')
          assert symbol.count(' ') == 0, ('Regex matched too much, probably got '
                                          'undecorated name as well')
          # Avoid getting names exported with dllexport, to avoid
          # "lld-link: error: duplicate /export option" on symbols such as:
          # ?kHexChar@numbers_internal@absl@@3QBDB
          if symbol in dll_exports:
            continue
          absl_symbols.add(symbol)

    logging.info('[%s - %s] Found %d absl symbols.', cpu, flavor, len(absl_symbols))

    if extra_gn_args:
      def_file = os.path.join('third_party', 'abseil-cpp',
                              'symbols_{}_{}_{}.def'.format(cpu, flavor, suffix))
    else:
      def_file = os.path.join('third_party', 'abseil-cpp',
                             'symbols_{}_{}.def'.format(cpu, flavor))

    with open(def_file, 'w', newline='') as f:
      f.write('EXPORTS\n')
      for s in sorted(absl_symbols):
        f.write('    {}\n'.format(s))

    # Hack, it looks like there is a race in the directory cleanup.
    time.sleep(10)

  logging.info('[%s - %s] .def file successfully generated.', cpu, flavor)


if __name__ == '__main__':
  logging.getLogger().setLevel(logging.INFO)

  if not os.getcwd().endswith('src') or not os.path.exists('chrome/browser'):
    logging.error('Run this script from a chromium/src/ directory.')
    exit(1)

  _GenerateDefFile('x86', True)
  _GenerateDefFile('x86', False)
  _GenerateDefFile('x64', True)
  _GenerateDefFile('x64', False)
  _GenerateDefFile('x64', False, ['is_asan = true'], 'asan')
  _GenerateDefFile('arm64', True)
  _GenerateDefFile('arm64', False)

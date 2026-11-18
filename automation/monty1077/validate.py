"""Exact-source CPU validation. All generated probe assets stay outside source trees."""
import datetime
import hashlib
import json
import math
import os
import pathlib
import random
import re
import signal
import struct
import subprocess
import sys
import time

ROOT = pathlib.Path(os.environ['GITHUB_WORKSPACE']).resolve()
E = ROOT / 'evidence'
E.mkdir(exist_ok=True)
HARNESS = pathlib.Path(__file__).resolve().parent
SOURCE = ROOT / 'source'
BASE = ROOT / 'baseline'
EXPECTED = {
    'source': ('057da5c2ee1e1bf25b18fa07aff9c8afb8a388c4', '985ad16b80e5a1a00c4f1566d7f0c0571f7cfaa1'),
    'baseline': ('2a0ba89d428b2846437815f60d577bba9f9c5211', 'b1908254988c9a55ebc1b6d8e6de6d8b95d6b5bc'),
}
RECEIPTS = []


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def save(name, value):
    (E / name).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(name, args, cwd=SOURCE, timeout=1200, required=True):
    start = time.monotonic()
    item = {'name': name, 'args': args, 'cwd': str(cwd), 'started_at': utc(), 'required': required}
    out, err = E / (name + '.stdout'), E / (name + '.stderr')
    with out.open('wb') as stdout, err.open('wb') as stderr:
        child = subprocess.Popen(args, cwd=cwd, stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            code = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            code = 124
            item['timeout'] = True
    item.update(exit_code=code, finished_at=utc(), elapsed_seconds=round(time.monotonic() - start, 3),
                stdout_sha256=sha(out), stderr_sha256=sha(err),
                stdout_bytes=out.stat().st_size, stderr_bytes=err.stat().st_size)
    RECEIPTS.append(item)
    save('commands.json', RECEIPTS)
    print(json.dumps(item), flush=True)
    return code


def source_state(label):
    snapshots = {}
    for name, (head, tree) in EXPECTED.items():
        path = ROOT / name
        def git(*args):
            result = subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True)
            return {'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
        state = {'head': git('rev-parse', 'HEAD'), 'tree': git('rev-parse', 'HEAD^{tree}'),
                 'tracked_diff': git('diff', 'HEAD', '--exit-code'),
                 'status': git('status', '--porcelain=v1', '--untracked-files=all')}
        state['valid'] = (state['head']['exit_code'] == state['tree']['exit_code'] == 0
                          and state['head']['stdout'].strip() == head
                          and state['tree']['stdout'].strip() == tree
                          and state['tracked_diff']['exit_code'] == 0
                          and state['status']['exit_code'] == 0
                          and state['status']['stdout'] == '')
        snapshots[name] = state
    save('source-state-' + label + '.json', {'at': utc(), 'sources': snapshots})
    return all(state['valid'] for state in snapshots.values())


def require_clean(label):
    if not source_state(label):
        raise RuntimeError('Source changed or not clean after ' + label)


def cases():
    values = [
        (1e20, 3), (710.8655, 3), (2.675, 2), (2.685, 2), (1.125, 2), (1.375, 2),
        (2.5, 0), (3.5, 0), (1250.0, -2), (1350.0, -2), (1e20, -20), (1e20, -21),
        (1.7976931348623157e308, 3), (1e308, -308), (1.49e308, -308),
        (2.2250738585072014e-308, 308), (5e-324, 309), (5e-324, 323), (5e-324, 324),
        (1.5e-323, 323), (0.0, 3), (0.5, 0), (0.04, 1), (4.0, -1),
        (2.0**-75, 0), (2.0**-76, 0), (2.0**127, 0), (2.0**128, 0),
        (1.25, 22), (7.0, 22), (7.0, 23), (2.0**127, -38), (2.0**127, -39),
        (0.1, 20), (3.4028236692093843e38, -35),
        (562949953421312.0, -15), (1.7976931348623157e308, -6),
        (-9.144331907742847e201, -18), (126.701, 29), (-826475.957213, 24),
        (237424185373.92227, 7), (4.656612873077393e-10, 23), (1.79e308, -308),
    ]
    expressions = [f'round({sign * value!r}, {digits})'
                   for value, digits in values for sign in (1.0, -1.0)]
    for value in (0.0, 1.2345678901234567, 5e-324, 1.7976931348623157e308):
        for sign in (1.0, -1.0):
            for digits in (324, 2**63 - 1, 2**63, 10**100, -309, -(2**63), -(2**63) - 1, -(10**100)):
                expressions.append(f'round({sign * value!r}, {digits})')
    for value in ("float('inf')", "float('-inf')", "float('nan')"):
        for digits in (0, 3, -3, 323, 324, -308, -309, 2**63, -(10**100)):
            expressions.append(f'round({value}, {digits})')
    rng = random.Random(107720261118)
    for _ in range(128):
        value = struct.unpack('!d', rng.getrandbits(64).to_bytes(8, 'big'))[0]
        if math.isfinite(value):
            for digits in (-309, -308, -38, -18, -15, -6, -2, -1, 0, 1, 2, 3, 7, 20, 22, 23, 29, 323, 324):
                expressions.append(f'round({value!r}, {digits})')
    expressions = list(dict.fromkeys(expressions))
    expected = []
    for expression in expressions:
        try:
            value = eval(expression, {'__builtins__': {'round': round, 'float': float}})
            assert type(value) is float
            item = {'expression': expression, 'kind': 'float', 'bits': struct.pack('!d', value).hex(),
                    'nan': math.isnan(value)}
        except Exception as error:
            item = {'expression': expression, 'kind': 'error', 'class': type(error).__name__, 'message': str(error)}
        expected.append(item)
    (E / 'expressions.txt').write_text('\n'.join(expressions) + '\n')
    blocks = []
    for index, expression in enumerate(expressions):
        blocks.append(
            'try:\n'
            f'    value = {expression}\n'
            '    if type(value) == float:\n'
            f'        print({index}, "OK", "Float(" + repr(value) + ")", sep="\\t")\n'
            '    else:\n'
            f'        print({index}, "OTHER", repr(value), sep="\\t")\n'
            'except Exception as error:\n'
            f'    print({index}, "ERROR", type(error).__name__ + ": " + str(error), sep="\\t")\n'
        )
    (E / 'public-cli-probe.py').write_text('\n'.join(blocks))
    save('cpython-expected.json', expected)
    return expected


def probe(name, path, expected):
    if command(name + '-build', ['cargo', 'build', '--locked', '-p', 'monty-runtime'], path) != 0:
        raise RuntimeError('Could not build exact ' + name + ' native runtime')
    require_clean(name + '-build')
    if command(name + '-help', ['cargo', 'run', '--quiet', '--locked', '-p', 'monty-runtime', '--', '--help'], path) != 0:
        raise RuntimeError('Public CLI help failed')
    help_text = (E / (name + '-help.stdout')).read_text()
    assert '--max-feed-duration' in help_text and '--max-turn-duration' in help_text
    if command(name + '-probe', ['cargo', 'run', '--quiet', '--locked', '-p', 'monty-runtime', '--',
               '--max-feed-duration', '30', '--max-turn-duration', '30',
               str(E / 'public-cli-probe.py')], path, timeout=600) != 0:
        raise RuntimeError('Public CLI probe execution failed')
    actual = (E / (name + '-probe.stdout')).read_text().splitlines()
    assert len(actual) == len(expected), (name, len(actual), len(expected))
    differences = []
    for index, (line, wanted) in enumerate(zip(actual, expected)):
        found_index, status, data = line.split('\t', 2)
        assert int(found_index) == index
        match = re.fullmatch(r'Float\((.+)\)', data) if status == 'OK' else None
        if wanted['kind'] == 'float':
            try:
                value = float(match.group(1)) if match else None
                equal = value is not None and ((wanted['nan'] and math.isnan(value))
                        or struct.pack('!d', value).hex() == wanted['bits'])
            except ValueError:
                equal = False
        else:
            equal = status == 'ERROR' and wanted['class'] in data and wanted['message'] in data
        if not equal:
            differences.append({'index': index, 'expected': wanted, 'actual_status': status, 'actual': data})
    result = {'source': name, 'cases': len(expected), 'mismatch_count': len(differences), 'differences': differences}
    save(name + '-probe-results.json', result)
    require_clean(name + '-probe')
    return result


if '--final-only' in sys.argv:
    raise SystemExit(0 if source_state('always-final') else 1)

failure = None
summary = {'started_at': utc(), 'event_commit': os.environ.get('GITHUB_SHA'),
           'source_commit': EXPECTED['source'][0], 'source_tree': EXPECTED['source'][1],
           'baseline_commit': EXPECTED['baseline'][0], 'baseline_tree': EXPECTED['baseline'][1],
           'python': sys.version, 'python_executable': sys.executable,
           'no_source_test_overlay': True, 'docs_runtime_test_executed': False}
try:
    assert sys.version_info[:2] == (3, 14), sys.version
    require_clean('initial')
    assert sha(SOURCE / 'Cargo.lock') == sha(BASE / 'Cargo.lock')
    os.environ['PYO3_PYTHON'] = sys.executable
    for name, args in [('rustc-version', ['rustc', '--version']), ('cargo-version', ['cargo', '--version']),
                       ('uv-version', ['uv', '--version']), ('python-version', [sys.executable, '--version'])]:
        if command(name, args, ROOT) != 0:
            raise RuntimeError(name)
    if command('frozen-python-bootstrap', ['make', 'install-py']) != 0:
        raise RuntimeError('Frozen normal Python bootstrap failed')
    require_clean('python-bootstrap')
    if command('locked-rust-fetch', ['cargo', 'fetch', '--locked']) != 0:
        raise RuntimeError('Locked Rust bootstrap failed')
    os.environ['CARGO_NET_OFFLINE'] = 'true'
    require_clean('rust-bootstrap')
    expected = cases()
    baseline = probe('baseline', BASE, expected)
    summary['baseline_probe'] = {'cases': baseline['cases'], 'mismatches': baseline['mismatch_count']}
    assert baseline['mismatch_count'] > 0
    assert any(item['expected']['kind'] == 'error' for item in baseline['differences']), 'No overflow baseline'
    assert any(item['expected']['expression'] == 'round(562949953421312.0, -15)' for item in baseline['differences'])
    command('baseline-make-test-no-features', ['make', 'test-no-features'], BASE, required=False)
    require_clean('baseline-full-tests')
    if command('candidate-make-format-rs', ['make', 'format-rs']) != 0:
        raise RuntimeError('Required Rust formatting command failed')
    require_clean('required-format-rs')
    candidate = probe('candidate', SOURCE, expected)
    summary['candidate_probe'] = {'cases': candidate['cases'], 'mismatches': candidate['mismatch_count']}
    assert candidate['mismatch_count'] == 0, 'Candidate differs from genuine CPython'
    command('candidate-focused-fixture', ['cargo', 'run', '--locked', '-p', 'monty-datatest', 'builtin__math_funcs'])
    require_clean('focused-fixture')
    command('candidate-make-test-no-features', ['make', 'test-no-features'])
    require_clean('candidate-full-tests')
    command('candidate-make-lint-rs', ['make', 'lint-rs'])
    require_clean('required-lint-rs')
    command('candidate-make-lint-py', ['make', 'lint-py'])
    require_clean('lint-py')
    command('candidate-make-lint-md', ['make', 'lint-md'])
    require_clean('lint-md')
except Exception as error:
    failure = repr(error)
finally:
    clean = source_state('driver-final')
    summary.update(finished_at=utc(), failure=failure, final_sources_clean=clean,
                   required_command_failures=[item['name'] for item in RECEIPTS if item['required'] and item['exit_code']],
                   commands=RECEIPTS)
    summary['passed'] = failure is None and clean and not summary['required_command_failures']
    save('SUMMARY.json', summary)
    print(json.dumps(summary), flush=True)
raise SystemExit(0 if summary['passed'] else 1)

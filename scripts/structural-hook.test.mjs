import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, readFileSync, chmodSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const hook = resolve('.githooks/pre-commit');
const posix = value => process.platform === 'win32' ? value.replaceAll('\\', '/').replace(/^([A-Za-z]):/, (_, drive) => '/' + drive.toLowerCase()) : value;
function exercise({ installed = true, version = 'doxguard 0.2.0', versionExit = 0, scanExit = 0, omitExit = 0, config } = {}) {
  const root = mkdtempSync(join(tmpdir(), 'hook-synthetic-'));
  const log = join(root, 'calls.txt');
  try {
    if (installed) {
      const adapter = join(root, 'doxguard');
      writeFileSync(adapter, '#!/bin/sh\nif [ "$1" = --version ]; then printf "%s\\n" "$SYN_VERSION"; exit "$SYN_VERSION_EXIT"; fi\nprintf "scan\\n" >> "$SYN_LOG"\nfor arg do printf "arg=%s\\n" "$arg" >> "$SYN_LOG"; done\nexit "$SYN_SCAN_EXIT"\n');
      chmodSync(adapter, 0o755);
    }
    const omit = join(root, 'omitnix');
    writeFileSync(omit, '#!/bin/sh\nprintf "omit=%s\\n" "$1" >> "$SYN_LOG"\nexit "$SYN_OMIT_EXIT"\n');
    chmodSync(omit, 0o755);
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !/^(DOXGUARD.*|GIT_.*|KB_ROOT|FAMILY_ROOT)$/i.test(key)));
    Object.assign(env, { SYN_VERSION: version, SYN_VERSION_EXIT: String(versionExit), SYN_SCAN_EXIT: String(scanExit), SYN_OMIT_EXIT: String(omitExit), SYN_LOG: posix(log) });
    if (config !== undefined) env.DOXGUARD_CONFIG = config;
    const shell = process.platform === 'win32' ? 'C:/Program Files/Git/bin/bash.exe' : '/bin/sh';
    const result = spawnSync(shell, ['-c', 'PATH="$1"; export PATH; exec /bin/sh "$2"', 'synthetic', posix(root), posix(hook)], { cwd: root, env, encoding: 'utf8', timeout: 10_000, windowsHide: true });
    assert.ifError(result.error);
    const calls = (() => { try { return readFileSync(log, 'utf8'); } catch { return ''; } })();
    return { exit: result.status, calls, actionable: String(result.stderr).includes('npm install --global doxguard@0.2.0') };
  } finally { rmSync(root, { recursive: true, force: true }); }
}

test('hook installed exact release scans staged strict then independent omitnix', () => {
  const value = exercise();
  assert.equal(value.exit, 0);
  assert.equal(value.calls, 'scan\narg=scan\narg=--staged\narg=--block\narg=--strict\narg=--config\narg=doxguard.ci.json\nomit=--check\n');
});
test('hook preserves inherited private config as one argument', () => {
  const config = 'synthetic config folder/private.json';
  const value = exercise({ config });
  assert.equal(value.exit, 0);
  assert.ok(value.calls.includes('arg=--config\narg=' + config + '\nomit=--check\n'));
});
test('hook missing installed scanner fails closed with installation instruction', () => {
  const value = exercise({ installed: false });
  assert.equal(value.exit, 2); assert.equal(value.calls, ''); assert.ok(value.actionable);
});
test('hook wrong, suffixed or failed version never scans or runs later checks', () => {
  for (const options of [{ version: 'doxguard 0.1.0' }, { version: 'doxguard 0.2.0-extra' }, { versionExit: 7 }]) {
    const value = exercise(options);
    assert.equal(value.exit, 2); assert.equal(value.calls, ''); assert.ok(value.actionable);
  }
});
test('hook propagates scan failure and does not execute omitnix', () => {
  for (const scanExit of [1, 2, 7]) {
    const value = exercise({ scanExit });
    assert.equal(value.exit, scanExit); assert.ok(!value.calls.includes('omit='));
  }
});
test('hook preserves omitnix failure exit handling', () => {
  const value = exercise({ omitExit: 7 });
  assert.equal(value.exit, 1); assert.ok(value.calls.endsWith('omit=--check\n'));
});

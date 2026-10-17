import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { inspect, isolatedEnv, nativeArgs, packagePin, passed, scanPair, validateConfig } from './structural-ci.mjs';
const report = (kind, hits = []) => ({
  scanned: 3, total_files: 3, exempt_or_skipped: 0, structural_patterns: 4, hits,
  ...(kind === 'legacy' ? { kb_needles: 0, family_needles: 0 } : {
    watchlist_needles: 0, coverage_skips: 0, watchlist_statistics: { candidates: 0, loaded: 0 },
    baseline_applied: false, baseline_suppressed_hits: 0,
  }),
});
const result = (kind, exit = 0, hits = []) => ({ status: exit, stdout: JSON.stringify(report(kind, hits)), stderr: '' });
test('exact release, strict explicit config, same checkout and both scanners after either failure', () => {
  assert.equal(packagePin, 'doxguard@0.2.0');
  assert.ok(nativeArgs.includes('--package=doxguard@0.2.0'));
  for (const exits of [[0, 0], [1, 0], [0, 1], [1, 1], [-1, 0]]) {
    const calls = [];
    const summaries = scanPair('/fixture', '/public-config.json', (cmd, args, cwd) => {
      const index = calls.length;
      calls.push({ cmd, args, cwd });
      return result(index === 0 ? 'legacy' : 'doxguard', exits[index]);
    });
    assert.equal(calls.length, 2);
    assert.ok(calls.every(call => call.cwd === '/fixture'));
    for (const flag of ['--strict', '--block', '--all-tracked', '--format=json', '--config', '/public-config.json']) assert.ok(calls[1].args.includes(flag));
    assert.equal(summaries.every(passed), exits.every(exit => exit === 0));
  }
});
test('raw values, source lines, stderr, errors never enter summaries', () => {
  const sentinel = 'SYNTHETIC_NOT_FOR_LOGS';
  for (const kind of ['legacy', 'doxguard']) {
    const value = result(kind, 1, [{ matched: sentinel, file: sentinel, source: sentinel, line: sentinel }]);
    value.stderr = sentinel;
    const summary = inspect(value, kind);
    assert.equal(summary.hits, 1);
    assert.ok(!JSON.stringify(summary).includes(sentinel));
    assert.ok(!JSON.stringify(inspect({ error: new Error(sentinel), status: null }, kind)).includes(sentinel));
  }
});
test('invalid JSON, missing fields, errors, watchlists, baselines and coverage fail closed', () => {
  for (const kind of ['legacy', 'doxguard']) {
    for (const invalid of ['', '{}', 'null']) assert.equal(passed(inspect({ status: 0, stdout: invalid }, kind)), false);
    assert.equal(passed(inspect({ ...result(kind), signal: 'SIGTERM' }, kind)), false);
  }
  for (const change of [{watchlist_needles: 1}, {coverage_skips: 1}, {baseline_applied: true}, {baseline_suppressed_hits: 1}, {watchlist_statistics: { candidates: 1, loaded: 0 }}]) {
    assert.equal(passed(inspect({status: 0, stdout: JSON.stringify({...report('doxguard'), ...change})}, 'doxguard')), false);
  }
  assert.equal(passed(inspect({...result('legacy'), stderr:'unexpected warning'}, 'legacy')), false);
  assert.equal(passed(inspect({status:0,stdout:JSON.stringify({...report('legacy'),kb_needles:1})}, 'legacy')), false);
});
test('public config has no watchlists or broad exemptions; all four structural rules enabled', () => {
  const config = JSON.parse(readFileSync(new URL('../doxguard.ci.json', import.meta.url), 'utf8'));
  assert.ok(validateConfig(config));
  assert.deepEqual(config.allow.emails, ['ishizakahiroshi.dev@gmail.com']); // doxguard: allow ishizakahiroshi.dev@gmail.com
  assert.deepEqual(config.allow.emailDomains, ['example.com', 'example.invalid']);
  for (const change of [{watchlists:[{}]}, {exemptPaths:['tests/']}, {includeDefaultExemptPaths:true}, {failOnSkip:false}, {structural:{}}]) assert.equal(validateConfig({...config,...change}),false);
  assert.deepEqual(isolatedEnv({KB_ROOT:'synthetic',FAMILY_ROOT:'synthetic',DOXGUARD_CONFIG:'synthetic',DOXGUARD_BASELINE:'synthetic',PATH:'keep'}),{PATH:'keep'});
});

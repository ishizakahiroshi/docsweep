#!/usr/bin/env node
// Public structural-only gate. Raw child output stays in memory, never CI logs.
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const packagePin = 'doxguard@0.2.0';
export const nativeArgs = ['exec', '--yes', '--package=' + packagePin, '--', 'doxguard'];
export function isolatedEnv(env = process.env) {
  const result = { ...env };
  for (const key of Object.keys(result)) {
    if (/^(KB_ROOT|FAMILY_ROOT|DOXGUARD.*)$/i.test(key)) delete result[key];
  }
  return result;
}
export function run(command, args, cwd = process.cwd()) {
  return spawnSync(command, args, {
    cwd, env: isolatedEnv(), encoding: 'utf8', maxBuffer: 32 * 1024 * 1024,
    timeout: 180_000, windowsHide: true,
  });
}
export function native(args, cwd, execute = run) {
  return execute(process.platform === 'win32' ? 'npm.cmd' : 'npm', [...nativeArgs, ...args], cwd);
}
export function inspect(result, kind) {
  // No exception message or untrusted field is forwarded to the log.
  const summary = { scanner: kind, exit: Number.isInteger(result.status) ? result.status : -1, valid: false };
  try {
    if (result.error || result.signal) return summary;
    const data = JSON.parse(result.stdout);
    const numeric = ['scanned', 'total_files', 'exempt_or_skipped', 'structural_patterns'];
    numeric.push(...(kind === 'legacy' ? ['kb_needles', 'family_needles'] : ['watchlist_needles', 'coverage_skips']));
    if (!numeric.every(key => Number.isSafeInteger(data[key]) && data[key] >= 0) || !Array.isArray(data.hits)) return summary;
    summary.scanned = data.scanned;
    summary.total = data.total_files;
    summary.exempt_or_skipped = data.exempt_or_skipped;
    summary.hits = data.hits.length;
    if (kind === 'legacy') {
      summary.needles = data.kb_needles + data.family_needles;
      // Legacy has no machine-readable coverage field. Any unexpected warning
      // fails this gate, including oversize/read errors. Native checks all files.
      const lines = String(result.stderr || '').split('\n').filter(Boolean);
      const expected = [
        'WARN: KB_ROOT env var not set — kb-derived watchlist skipped, structural regex only',
        'WARN: KB_ROOT env var が未設定 — kb 由来 watchlist をスキップ・構造 regex のみで継続',
        'WARN: FAMILY_ROOT env var not set — family watchlist skipped',
        'WARN: FAMILY_ROOT env var が未設定 — family watchlist をスキップ',
      ];
      summary.coverage_warnings = lines.filter(line => !expected.includes(line)).length;
      summary.valid = data.kb_needles === 0 && data.family_needles === 0 && summary.coverage_warnings === 0;
    } else {
      summary.needles = data.watchlist_needles;
      summary.coverage_skips = data.coverage_skips;
      summary.valid = data.watchlist_needles === 0 && data.watchlist_statistics?.candidates === 0
        && data.watchlist_statistics?.loaded === 0 && data.coverage_skips === 0
        && data.baseline_applied === false && data.baseline_suppressed_hits === 0;
    }
    summary.valid &&= data.structural_patterns === 4 && data.scanned > 0 && data.scanned <= data.total_files;
  } catch { /* Invalid or incomplete output fails closed. */ }
  return summary;
}
export function scanPair(cwd, configPath, execute = run) {
  // Neither child failure short-circuits the other scanner.
  const legacy = execute(process.execPath, ['scripts/secrets-scan.mjs', '--all-tracked', '--block', '--format=json'], cwd);
  const modern = native(['scan', '--all-tracked', '--block', '--strict', '--format=json', '--config', configPath], cwd, execute);
  return [inspect(legacy, 'legacy'), inspect(modern, 'doxguard')];
}
export function passed(summary) {
  return summary.valid && summary.exit === 0 && summary.hits === 0;
}
export function validateConfig(config) {
  return Array.isArray(config.watchlists) && config.watchlists.length === 0
    && config.requireWatchlists === false && config.failOnSkip === true
    && config.includeDefaultExemptPaths === false && Array.isArray(config.exemptPaths) && config.exemptPaths.length === 0
    && ['windowsPath', 'posixHome', 'privateIp', 'email'].every(key => config.structural?.[key] === true)
    && config.allow?.disallowBareAllow === true && config.allow?.disallowInlineAllow !== true;
}
export function main() {
  const cwd = process.cwd();
  if (process.argv.includes('--legacy-only')) {
    const summary = inspect(run(process.execPath, ['scripts/secrets-scan.mjs', '--all-tracked', '--block', '--format=json'], cwd), 'legacy');
    console.log(JSON.stringify(summary));
    return passed(summary) ? 0 : 1;
  }
  const configPath = resolve(cwd, 'doxguard.ci.json');
  let configValid = false;
  try { configValid = validateConfig(JSON.parse(readFileSync(configPath, 'utf8'))); } catch { /* fail closed */ }
  const sha = run('git', ['rev-parse', 'HEAD'], cwd);
  const sourceSha = String(sha.stdout || '').trim();
  const shaValid = sha.status === 0 && /^[a-f0-9]{40}$/.test(sourceSha);
  console.log('source_sha=' + (shaValid ? sourceSha : 'unavailable'));
  console.log('legacy_command=node scripts/secrets-scan.mjs --all-tracked --block --format=json');
  console.log('native_command=npm exec --yes --package=doxguard@0.2.0 -- doxguard scan --all-tracked --block --strict --format=json --config doxguard.ci.json');
  // Version verification is independent of the legacy scan. A missing package
  // must still leave evidence for legacy, and must never make the gate green.
  const version = native(['--version'], cwd);
  const versionValid = version.status === 0 && /^doxguard 0\.2\.0\s*$/.test(version.stdout || '');
  console.log('native_version=' + (versionValid ? '0.2.0' : 'unavailable-or-unexpected'));
  console.log('config_valid=' + configValid);
  const summaries = configValid ? scanPair(cwd, configPath) : [
    inspect(run(process.execPath, ['scripts/secrets-scan.mjs', '--all-tracked', '--block', '--format=json'], cwd), 'legacy'),
    { scanner: 'doxguard', valid: false, exit: -1 },
  ];
  for (const summary of summaries) console.log(JSON.stringify(summary));
  return configValid && shaValid && versionValid && summaries.every(passed) ? 0 : 1;
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.exitCode = main();

#!/usr/bin/env node
// Public structural-only gate. Raw child output stays in memory, never CI logs.
import { spawnSync } from 'node:child_process';
import { readFileSync, statSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { createHash } from 'node:crypto';
import { resolve, join, win32 } from 'node:path';
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
export function run(command, args, cwd = process.cwd(), timeout = 180_000) {
  return spawnSync(command, args, {
    cwd, env: isolatedEnv(), encoding: 'utf8', maxBuffer: 32 * 1024 * 1024,
    timeout, windowsHide: true, windowsVerbatimArguments: process.platform === 'win32' && win32.basename(command).toLowerCase() === 'cmd.exe',
  });
}
const unsafeWindowsArgument = /["%!?^&|<>\r\n\u0000]/;
export function resolveWindowsTools({ env = process.env, cwd = process.cwd(), isFile = path => {
  try { return statSync(path).isFile(); } catch { return false; }
} } = {}) {
  const value = name => env[Object.keys(env).find(key => key.toLowerCase() === name.toLowerCase())];
  const absolute = path => typeof path === 'string' && (/^[A-Za-z]:[\\/]/.test(path) || /^\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$)/.test(path));
  const root = value('SystemRoot');
  if (!absolute(root) || unsafeWindowsArgument.test(root)) throw new Error('Windows system directory unavailable');
  const command = win32.join(root, 'System32', 'cmd.exe');
  if (!isFile(command)) throw new Error('Windows command processor unavailable');
  const current = absolute(cwd) ? win32.resolve(cwd).toLowerCase() : null;
  for (const entry of String(value('PATH') || '').split(';')) {
    const directory = entry.trim().replace(/^"(.*)"$/, '$1');
    // Only explicit absolute PATH directories are eligible. Never search cwd,
    // empty entries, or relative directories supplied by the checkout.
    if (!absolute(directory) || unsafeWindowsArgument.test(directory)) continue;
    const normalized = win32.resolve(directory);
    if (normalized.toLowerCase() === current) continue;
    const npm = win32.join(normalized, 'npm.cmd');
    if (isFile(npm)) return { command, npm };
  }
  throw new Error('npm command unavailable on absolute PATH');
}
export function nativeInvocation(args, platform = process.platform, windowsOptions) {
  const all = [...nativeArgs, ...args];
  if (platform !== 'win32') return { command: 'npm', args: all };
  // cmd expands percent/exclamation even in quotes. Reject shell syntax rather
  // than passing caller-controlled paths through an unsafe quoting shortcut.
  if (all.some(arg => unsafeWindowsArgument.test(arg))) throw new Error('unsupported Windows command argument');
  const { command, npm } = resolveWindowsTools(windowsOptions);
  return { command, args: ['/d', '/s', '/c', '""' + npm + '" ' + all.map(arg => '"' + arg + '"').join(' ') + '"'] };
}
export function native(args, cwd, execute = run) {
  try {
    const invocation = nativeInvocation(args, process.platform, { cwd: cwd ?? process.cwd() });
    return execute(invocation.command, invocation.args, cwd);
  } catch { return { status: -1, stdout: '', stderr: '', error: { code: 'INVALID_ARGUMENT' } }; }
}
export function failureReason(result) {
  if (result.signal) return 'interrupted';
  if (result.error?.code === 'ENOENT') return 'executable-or-interpreter-missing';
  if (result.error?.code === 'EACCES') return 'executable-permission';
  if (result.error?.code === 'ENOEXEC') return 'executable-format';
  if (result.error) return 'spawn-or-timeout';
  const stderr = String(result.stderr || '');
  if (/error while loading shared libraries|symbol lookup error|bad ELF interpreter/i.test(stderr)) return 'native-loader';
  if (/permission denied/i.test(stderr)) return 'executable-permission';
  if (/command not found|(?:^|[:\n])[^\n]*doxguard[^\n]*not found/i.test(stderr)) return 'command-not-found';
  if (/\b(?:E404|ETARGET)\b/.test(stderr)) return 'package-unavailable';
  if (/\b(?:ENOTFOUND|ECONNRESET|ETIMEDOUT|EAI_AGAIN)\b/.test(stderr)) return 'package-network';
  return 'invalid-or-missing-scanner-report';
}
export function exactNativeVersion(result) {
  return !result.error && !result.signal && result.status === 0 && /^doxguard 0\.2\.0\s*$/.test(result.stdout || '');
}
export function runtimePreflight({ execute = run, platform = process.platform, arch = process.arch } = {}) {
  const receipt = { preflight: 'linux-public-package', node: /^[0-9]+\.[0-9]+\.[0-9]+$/.test(process.versions.node) ? process.versions.node : 'unavailable', accepted: false };
  if (platform !== 'linux' || !['x64','arm64'].includes(arch)) return { ...receipt, reason: 'not-applicable' };
  let root;
  const deadline = Date.now() + 180_000;
  const bounded = (command,args,cwd) => {
    const remaining = deadline - Date.now();
    return remaining > 0 ? execute(command,args,cwd,remaining) : { status: -1, error: { code: 'ETIMEDOUT' } };
  };
  try {
    root = mkdtempSync(join(tmpdir(), 'structural-preflight-'));
    const npmVersion = bounded('npm', ['--version'], root);
    const version = String(npmVersion.stdout || '').trim();
    receipt.npm = npmVersion.status === 0 && /^[0-9]+\.[0-9]+\.[0-9]+$/.test(version) ? version : 'unavailable';
    const install = bounded('npm', ['install','--prefix',root,'--cache',join(root,'cache'),'--ignore-scripts','--no-audit','--no-fund','--package-lock=false','--include=optional',packagePin], root);
    if (install.status !== 0 || install.error || install.signal) return { ...receipt, reason: failureReason(install) };
    const modules = join(root, 'node_modules');
    const rootPackage = JSON.parse(readFileSync(join(modules,'doxguard','package.json'),'utf8'));
    const platformName = '@ishizakahiroshi/doxguard-linux-' + arch;
    const platformDir = join(modules, '@ishizakahiroshi', 'doxguard-linux-' + arch);
    const platformPackage = JSON.parse(readFileSync(join(platformDir,'package.json'),'utf8'));
    if (rootPackage.name !== 'doxguard' || rootPackage.version !== '0.2.0' || platformPackage.name !== platformName || platformPackage.version !== '0.2.0') return { ...receipt, reason: 'package-identity-mismatch' };
    const binary = join(platformDir,'bin','doxguard');
    receipt.payload_sha256 = createHash('sha256').update(readFileSync(binary)).digest('hex');
    receipt.payload_executable = (statSync(binary).mode & 0o111) !== 0;
    const direct = bounded(binary, ['--version'], root);
    receipt.direct = exactNativeVersion(direct) ? 'version-ok' : failureReason(direct);
    const launcher = bounded(process.execPath,[join(modules,'doxguard','bin','doxguard.js'),'--version'],root);
    receipt.launcher = exactNativeVersion(launcher) ? 'version-ok' : failureReason(launcher);
    const viaExec = bounded('npm',[...nativeArgs,'--version'],root);
    receipt.npm_exec = exactNativeVersion(viaExec) ? 'version-ok' : failureReason(viaExec);
    receipt.accepted = receipt.direct === 'version-ok' && receipt.launcher === 'version-ok' && receipt.npm_exec === 'version-ok';
    return receipt;
  } catch { return { ...receipt, reason: 'preflight-setup-or-payload-missing' }; }
  finally {
    try { if (root) rmSync(root,{recursive:true,force:true}); }
    catch { return { ...receipt, accepted: false, reason: 'preflight-cleanup-failed' }; }
  }
}
export function safeFindings(hits) {
  const fold = value => value.toLowerCase().replace(/[\\/]+/g, '/');
  const needles = hits.map(hit => hit?.matched).filter(value => typeof value === 'string' && value.length > 0).map(fold);
  const clean = value => {
    if (typeof value !== 'string' || value.length > 1024 || /[\u0000-\u001f\u007f\u202a-\u202e\u2066-\u2069]/.test(value)) return '[metadata omitted]';
    return needles.some(needle => fold(value).includes(needle)) ? '[redacted metadata]' : value;
  };
  const sources = new Set(['structural: Windows absolute path', 'structural: Windows personal absolute path', 'structural: POSIX home path', 'structural: Private IPv4 (RFC1918)', 'structural: Email address (not on public allowlist)']);
  return hits.map(hit => ({
    file: clean(hit?.file),
    line: Number.isSafeInteger(hit?.lineNumber ?? hit?.line_number) && (hit.lineNumber ?? hit.line_number) > 0 ? (hit.lineNumber ?? hit.line_number) : null,
    kind: ['structural', 'watchlist'].includes(hit?.kind) ? hit.kind : 'unknown',
    source: sources.has(hit?.source) ? clean(hit.source) : 'unrecognized-detector',
  }));
}
export function inspect(result, kind) {
  // No exception message or untrusted field is forwarded to the log.
  const summary = { scanner: kind, exit: Number.isInteger(result.status) ? result.status : -1, valid: false, failure_reason: failureReason(result) };
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
    summary.findings = safeFindings(data.hits);
    delete summary.failure_reason;
    if (kind === 'legacy') {
      summary.needles = data.kb_needles + data.family_needles;
      summary.coverage_skips = 'not-reported-by-legacy';
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
export function matchesExpectedSha(sourceSha, expectedSha, inCI) {
  return /^[a-f0-9]{40}$/.test(sourceSha)
    && (expectedSha === undefined ? !inCI : /^[a-f0-9]{40}$/.test(expectedSha) && sourceSha === expectedSha);
}
export function main() {
  const cwd = process.cwd();
  const sha = run('git', ['rev-parse', 'HEAD'], cwd);
  const sourceSha = String(sha.stdout || '').trim();
  const expectedSha = process.env.STRUCTURAL_EXPECTED_SHA;
  const shaValid = sha.status === 0 && matchesExpectedSha(sourceSha, expectedSha, process.env.CI);
  console.log('source_sha=' + (/^[a-f0-9]{40}$/.test(sourceSha) ? sourceSha : 'unavailable'));
  console.log('expected_sha_match=' + shaValid);
  if (process.argv.includes('--legacy-only')) {
    const summary = inspect(run(process.execPath, ['scripts/secrets-scan.mjs', '--all-tracked', '--block', '--format=json'], cwd), 'legacy');
    console.log(JSON.stringify(summary));
    return shaValid && passed(summary) ? 0 : 1;
  }
  const configPath = resolve(cwd, 'doxguard.ci.json');
  let configValid = false;
  try { configValid = validateConfig(JSON.parse(readFileSync(configPath, 'utf8'))); } catch { /* fail closed */ }
  console.log('legacy_command=node scripts/secrets-scan.mjs --all-tracked --block --format=json');
  console.log('native_command=npm exec --yes --package=doxguard@0.2.0 -- doxguard scan --all-tracked --block --strict --format=json --config doxguard.ci.json');
  // Version verification is independent of the legacy scan. A missing package
  // must still leave evidence for legacy, and must never make the gate green.
  const version = native(['--version'], cwd);
  const versionValid = exactNativeVersion(version);
  console.log('native_version=' + (versionValid ? '0.2.0' : 'unavailable-or-unexpected'));
  console.log('config_valid=' + configValid);
  const summaries = configValid ? scanPair(cwd, configPath) : [
    inspect(run(process.execPath, ['scripts/secrets-scan.mjs', '--all-tracked', '--block', '--format=json'], cwd), 'legacy'),
    { scanner: 'doxguard', valid: false, exit: -1 },
  ];
  for (const summary of summaries) console.log(JSON.stringify(summary));
  // Runtime evidence for both gates is emitted before bounded diagnostics.
  const preflight = process.env.CI ? runtimePreflight() : { accepted: false, reason: 'not-requested-outside-ci' };
  console.log(JSON.stringify(preflight));
  return (preflight.accepted || ['not-applicable','not-requested-outside-ci'].includes(preflight.reason)) && configValid && shaValid && versionValid && summaries.every(passed) ? 0 : 1;
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.exitCode = main();

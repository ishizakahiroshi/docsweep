#!/usr/bin/env node
// Public structural-only gate. Raw child output stays in memory, never CI logs.
import { spawnSync } from 'node:child_process';
import { readFileSync, statSync, lstatSync, realpathSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { createHash } from 'node:crypto';
import { resolve, join, win32 } from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

export const packagePin = 'doxguard@0.2.0';
export function isolatedEnv(env = process.env) {
  const result = { ...env };
  for (const key of Object.keys(result)) {
    if (/^(KB_ROOT|FAMILY_ROOT|DOXGUARD.*|GIT_.*)$/i.test(key)) delete result[key];
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
    const scanner = win32.join(normalized, 'doxguard.cmd');
    if (isFile(scanner)) return { command, scanner };
  }
  throw new Error('installed doxguard command unavailable on absolute PATH');
}
export function nativeInvocation(args, platform = process.platform, windowsOptions) {
  const all = [...args];
  if (platform !== 'win32') return { command: 'doxguard', args: all };
  // cmd expands percent/exclamation even in quotes. Reject shell syntax rather
  // than passing caller-controlled paths through an unsafe quoting shortcut.
  if (all.some(arg => unsafeWindowsArgument.test(arg))) throw new Error('unsupported Windows command argument');
  const { command, scanner } = resolveWindowsTools(windowsOptions);
  return { command, args: ['/d', '/s', '/c', '""' + scanner + '" ' + all.map(arg => '"' + arg + '"').join(' ') + '"'] };
}
export function nativeRoute(inCI = process.env.CI, platform = process.platform) {
  return inCI && platform === 'linux' ? 'verified-root-launcher' : 'installed-exact-0.2.0';
}
export function native(args, cwd, execute = run, invocationOptions = {}) {
  try {
    if (nativeRoute() === 'verified-root-launcher' && execute === run) {
      const runtime = getPublicRuntime();
      if (!runtime.ready) return runtime.failure;
      const identity = verifyRuntime(runtime.root);
      console.log(JSON.stringify({ runtime_use: args[0] === '--version' ? 'version' : 'scan', ...identity.receipt }));
      return execute(process.execPath, [identity.launcher, ...args], cwd);
    }
    const invocation = nativeInvocation(args, invocationOptions.platform ?? process.platform,
      invocationOptions.windowsOptions ?? { cwd: cwd ?? process.cwd() });
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
  if (/error while loading shared libraries|symbol lookup error|bad ELF interpreter|(?:GLIBC|GLIBCXX|CXXABI)[^\n]*not found/i.test(stderr)) return 'native-loader';
  if (/permission denied/i.test(stderr)) return 'executable-permission';
  if (/command not found|(?:^|[:\n])[^\n]*doxguard[^\n]*not found/i.test(stderr)) return 'command-not-found';
  if (/\b(?:E404|ETARGET)\b/.test(stderr)) return 'package-unavailable';
  if (/\b(?:ENOTFOUND|ECONNRESET|ETIMEDOUT|EAI_AGAIN)\b/.test(stderr)) return 'package-network';
  return 'invalid-or-missing-scanner-report';
}
// Accepted public release identities. Unknown architectures fail closed until
// their canonical release hashes are independently accepted.
export const canonicalRuntime = Object.freeze({
  arch: 'x64',
  payload: '5458f08f85753cf6d33ad869063af16ca8f55a800bb9d17a78c4b6adc0651279',
  // https://github.com/ishizakahiroshi/doxguard/blob/v0.2.0/bin/doxguard.js
  launcher: '5374a0b83149b3ca78ad897c2475c6dbbbb44b55c4c6671f762c4e2bd8312b28',
});
export function verifyRuntime(root, arch = process.arch, expected = canonicalRuntime, fileStat = statSync) {
  if (arch !== expected.arch) throw new Error('unsupported canonical architecture');
  const rootDir = join(root,'node_modules','doxguard');
  const platformName = '@ishizakahiroshi/doxguard-linux-' + arch;
  const platformDir = join(root,'node_modules','@ishizakahiroshi','doxguard-linux-' + arch);
  const a = JSON.parse(readFileSync(join(rootDir,'package.json'),'utf8'));
  const b = JSON.parse(readFileSync(join(platformDir,'package.json'),'utf8'));
  if (a.name !== 'doxguard' || a.version !== '0.2.0' || a.type !== 'module'
      || a.bin?.doxguard !== 'bin/doxguard.js' || a.optionalDependencies?.[platformName] !== '0.2.0'
      || b.name !== platformName || b.version !== '0.2.0' || b.bin?.doxguard !== 'bin/doxguard' || b.exports !== undefined
      || JSON.stringify(b.os) !== '["linux"]' || JSON.stringify(b.cpu) !== JSON.stringify([arch])) throw new Error('package identity mismatch');
  const launcher = join(rootDir,'bin','doxguard.js');
  const binary = join(platformDir,'bin','doxguard');
  for (const path of [rootDir, platformDir, launcher, binary, join(rootDir,'package.json'), join(platformDir,'package.json')]) {
    if (realpathSync(path) !== resolve(path)) throw new Error('redirected runtime path');
  }
  if (!fileStat(launcher).isFile() || !fileStat(binary).isFile() || !(fileStat(binary).mode & 0o111)) throw new Error('entrypoint mismatch');
  // Node's in-process resolver caches successful results. A fresh launcher
  // must not see a higher-priority package added after a prior verification.
  for (const directory of [join(rootDir,'bin','node_modules'),join(rootDir,'node_modules')]) {
    try {
      lstatSync(join(directory,'@ishizakahiroshi','doxguard-linux-' + arch));
      throw new Error('nested platform candidate');
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  // Resolve exactly as the canonical launcher does, excluding a nested or
  // redirected platform package with otherwise plausible outer metadata.
  if (realpathSync(createRequire(launcher).resolve(platformName + '/package.json')) !== realpathSync(join(platformDir,'package.json'))) throw new Error('platform resolution mismatch');
  const hash = path => createHash('sha256').update(readFileSync(path)).digest('hex');
  const payload = hash(binary), launch = hash(launcher);
  if (payload !== expected.payload || launch !== expected.launcher) throw new Error('canonical hash mismatch');
  return {launcher,binary,receipt:{package:'doxguard',version:'0.2.0',platform:'linux-' + arch,payload_sha256:payload,launcher_sha256:launch,canonical_match:true}};
}
let publicRuntime;
export function provisionPublicRuntime(execute = run, arch = process.arch) {
  const root = mkdtempSync(join(tmpdir(), 'structural-runtime-'));
  const failure = {status:-1,stdout:'',stderr:'',error:{code:'PUBLIC_RUNTIME_SETUP'}};
  try {
    if (arch !== canonicalRuntime.arch) throw new Error('unsupported architecture');
    const install = execute('npm',['install','--prefix',root,'--cache',join(root,'cache'),'--ignore-scripts','--no-audit','--no-fund','--package-lock=false','--include=optional',packagePin],root,180_000);
    if (install.status !== 0 || install.error || install.signal) return {ready:false,root,failure:install};
    return {ready:true,root,...verifyRuntime(root,arch)};
  } catch { return {ready:false,root,failure}; }
}
function getPublicRuntime() {
  if (!publicRuntime) {
    publicRuntime = provisionPublicRuntime();
    process.once('exit', () => { try { rmSync(publicRuntime.root,{recursive:true,force:true}); } catch { console.log('public_runtime_cleanup_failed=true'); process.exitCode = 1; } });
  }
  return publicRuntime;
}
export function exactNativeVersion(result) {
  return !result.error && !result.signal && result.status === 0 && /^doxguard 0\.2\.0\s*$/.test(result.stdout || '');
}
export function runtimePreflight({ execute = run, platform = process.platform, arch = process.arch, runtime = getPublicRuntime } = {}) {
  const receipt = { preflight: 'linux-public-package', node: /^[0-9]+\.[0-9]+\.[0-9]+$/.test(process.versions.node) ? process.versions.node : 'unavailable', accepted: false };
  if (platform !== 'linux' || arch !== canonicalRuntime.arch) return { ...receipt, reason: 'unsupported-canonical-platform' };
  const deadline = Date.now() + 180_000;
  const bounded = (command,args,cwd) => {
    const remaining = deadline - Date.now();
    return remaining > 0 ? execute(command,args,cwd,remaining) : { status: -1, error: { code: 'ETIMEDOUT' } };
  };
  try {
    // Reuse the actual scan installation, not a second diagnostic install.
    const actual = runtime();
    if (!actual.ready) return {...receipt,reason:failureReason(actual.failure)};
    const identity = verifyRuntime(actual.root,arch);
    Object.assign(receipt, identity.receipt);
    const npmVersion = bounded('npm',['--version'],actual.root);
    const version = String(npmVersion.stdout || '').trim();
    receipt.npm = npmVersion.status === 0 && /^[0-9]+\.[0-9]+\.[0-9]+$/.test(version) ? version : 'unavailable';
    const direct = bounded(identity.binary,['--version'],actual.root);
    receipt.direct = exactNativeVersion(direct) ? 'version-ok' : failureReason(direct);
    const launcher = bounded(process.execPath,[identity.launcher,'--version'],actual.root);
    receipt.launcher = exactNativeVersion(launcher) ? 'version-ok' : failureReason(launcher);
    receipt.accepted = receipt.direct === 'version-ok' && receipt.launcher === 'version-ok';
    receipt.selected_route = 'verified-root-launcher';
    return receipt;
  } catch { return { ...receipt, reason: 'runtime-identity-or-payload-mismatch' }; }
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
export function inspect(result) {
  // No exception message or untrusted field is forwarded to the log.
  const summary = { scanner: 'doxguard', exit: Number.isInteger(result.status) ? result.status : -1, valid: false, failure_reason: failureReason(result) };
  try {
    if (result.error || result.signal) return summary;
    const data = JSON.parse(result.stdout);
    const numeric = ['scanned', 'total_files', 'exempt_or_skipped', 'structural_patterns', 'watchlist_needles', 'coverage_skips'];
    if (!numeric.every(key => Number.isSafeInteger(data[key]) && data[key] >= 0) || !Array.isArray(data.hits)) return summary;
    summary.scanned = data.scanned;
    summary.total = data.total_files;
    summary.exempt_or_skipped = data.exempt_or_skipped;
    summary.hits = data.hits.length;
    summary.findings = safeFindings(data.hits);
    delete summary.failure_reason;
    summary.needles = data.watchlist_needles;
    summary.coverage_skips = data.coverage_skips;
    summary.valid = data.watchlist_needles === 0 && data.watchlist_statistics?.candidates === 0
      && data.watchlist_statistics?.loaded === 0 && data.coverage_skips === 0
      && data.baseline_applied === false && data.baseline_suppressed_hits === 0;
    summary.valid &&= data.structural_patterns === 4 && data.scanned > 0
      && data.scanned + data.exempt_or_skipped === data.total_files;
  } catch { /* Invalid or incomplete output fails closed. */ }
  return summary;
}
export function scanNative(cwd, configPath, execute = run, invocationOptions = {}) {
  const modern = native(['scan', '--all-tracked', '--block', '--strict', '--format=json', '--config', configPath], cwd, execute, invocationOptions);
  return inspect(modern);
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
  const configPath = resolve(cwd, 'doxguard.ci.json');
  let configValid = false;
  try { configValid = validateConfig(JSON.parse(readFileSync(configPath, 'utf8'))); } catch { /* fail closed */ }
  console.log('native_command=' + nativeRoute() + ' scan --all-tracked --block --strict --format=json --config doxguard.ci.json');
  // Exact version is required for the installed local or verified CI runtime.
  const version = native(['--version'], cwd);
  const versionValid = exactNativeVersion(version);
  console.log('native_version=' + (versionValid ? '0.2.0' : 'unavailable-or-unexpected'));
  console.log('config_valid=' + configValid);
  const summary = configValid && versionValid ? scanNative(cwd, configPath)
    : { scanner: 'doxguard', valid: false, exit: -1 };
  console.log(JSON.stringify(summary));
  // Runtime evidence is emitted before bounded diagnostics.
  const preflight = process.env.CI ? runtimePreflight() : { accepted: false, reason: 'not-requested-outside-ci' };
  console.log(JSON.stringify(preflight));
  return (preflight.accepted || ['not-requested-outside-ci'].includes(preflight.reason)) && configValid && shaValid && versionValid && passed(summary) ? 0 : 1;
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.exitCode = main();

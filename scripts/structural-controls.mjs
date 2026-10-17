#!/usr/bin/env node
// Real scanner controls use isolated, throwaway Git fixtures and no watchlists.
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { native, run, scanNative, passed, inspect, exactNativeVersion } from './structural-ci.mjs';

const config = resolve('doxguard.ci.json');
const slash = String.fromCharCode(92);
const win = ['C:', 'Users', 'fixture', 'document.txt'].join(slash);
const doubled = win.replaceAll(slash, slash + slash);
const home = ['', 'home', 'fixture', 'document.txt'].join('/');
const homePrefix = home.slice(0, home.lastIndexOf("/") + 1);
const ip = [10, 23, 45, 67].join('.');
const email = ['fixture', 'unlisted.test'].join('@');
const marker = ['doxguard:', 'allow'].join(' ');
const scoped = value => `${value} # ${marker} ${value}`;
const portable = ['relative/document.txt', ...['invalid', 'com'].map(suffix => ['fixture', 'example.' + suffix].join('@'))].join('\n');
const cases = [
  ['portable', portable, 0],
  ['windows', win, 1],
  ['doubled-windows', doubled, 1],
  ['home', home, 1],
  ['private-ip', ip, 1],
  ['unlisted-email', email, 1],
  ['scoped-ip', scoped(ip), 0],
  ['scoped-home', `${home} # ${marker} ${homePrefix}`, 0],
  ['scoped-windows', `${win} # ${marker} C:${slash}Users${slash}`, 0],
  ['scoped-doubled-windows', `${doubled} # ${marker} Users`, 0],
  ['bare-rejected', `${ip} # ${marker}`, 1],
  ['wrong-scope', `${ip} # ${marker} unrelated`, 1],
  ['short-scope', `${ip} # ${marker} 23`, 1],
  ['reverse-scope', `${ip} # ${marker} ${ip}-extra`, 1],
  ['other-hit-on-line', `${scoped(ip)} ${email}`, 1],
];
let failed = 0;
const root = mkdtempSync(join(tmpdir(), 'structural-controls-'));
try {
  if (!process.env.CI && !exactNativeVersion(native(['--version'], root))) throw new Error('installed exact version required');
  if (run('git', ['init', '-q'], root).status !== 0) throw new Error('fixture Git setup failed');
  for (const [id, content, nativeExit] of cases) {
    writeFileSync(join(root, 'fixture.txt'), content + '\n');
    if (run('git', ['add', 'fixture.txt'], root).status !== 0) throw new Error('fixture stage failed');
    const summary = scanNative(root, config);
    const ok = summary.valid && summary.exit === nativeExit
      && (summary.exit === 0 ? summary.hits === 0 : summary.hits > 0);
    console.log(JSON.stringify({ control: id, passed: ok, results: [summary] }));
    if (!ok) failed++;
  }
  // Coverage skips are a hard failure in strict mode, not a successful clean scan.
  const link = join(root, 'large.txt');
  writeFileSync(link, 'harmless text\n');
  const limited = join(root, 'limited.json');
  // This synthetic config is intentionally test-only and is not tracked.
  writeFileSync(limited, JSON.stringify({ watchlists: [], requireWatchlists: false, maxFileSize: 1 }));
  const result = native(['scan', '--path', link, '--block', '--strict', '--format=json', '--config', limited], root);
  const summary = inspect(result, 'doxguard');
  const ok = result.status === 1 && summary.coverage_skips === 1 && !passed(summary);
  console.log(JSON.stringify({ control: 'coverage-skip-blocks', passed: ok }));
  if (!ok) failed++;
} catch {
  console.log('control_setup_or_execution_failed=true');
  failed++;
} finally {
  try { rmSync(root, { recursive: true, force: true }); }
  catch { console.log('control_cleanup_failed=true'); failed++; }
}
console.log('controls_failed=' + failed);
process.exitCode = failed === 0 ? 0 : 1;

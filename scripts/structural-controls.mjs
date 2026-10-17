#!/usr/bin/env node
// Real scanner controls use isolated, throwaway Git fixtures and no watchlists.
import { mkdtempSync, mkdirSync, copyFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { native, run, scanPair, passed, inspect } from './structural-ci.mjs';

const config = resolve('doxguard.ci.json');
const slash = String.fromCharCode(92);
const win = ['C:', 'Users', 'fixture', 'document.txt'].join(slash);
const doubled = win.replaceAll(slash, slash + slash);
const home = ['', 'home', 'fixture', 'document.txt'].join('/');
const homePrefix = home.slice(0, home.lastIndexOf("/") + 1);
const ip = [10, 23, 45, 67].join('.');
const email = ['fixture', 'unlisted.test'].join('@');
const marker = ['doxguard:', 'allow'].join(' ');
const oldMarker = ['secrets-scan:', 'allow'].join(' ');
const scoped = value => `${value} # ${marker} ${value}; ${oldMarker} ${value}`;
const cases = [
  ['portable', 'relative/document.txt\nfixture@example.invalid\nfixture@example.com', 0, 0],
  ['windows', win, 1, 1],
  ['doubled-windows', doubled, 0, 1], // intentional new coverage over legacy
  ['home', home, 1, 1],
  ['private-ip', ip, 1, 1],
  ['unlisted-email', email, 1, 1],
  ['scoped-ip', scoped(ip), 0, 0],
  ['scoped-home', `${home} # ${marker} ${homePrefix}; ${oldMarker} ${homePrefix}`, 0, 0],
  ['scoped-windows', `${win} # ${marker} C:${slash}Users${slash}; ${oldMarker} C:${slash}Users${slash}`, 0, 0],
  ['scoped-doubled-windows', `${doubled} # ${marker} Users`, 0, 0],
  ['bare-rejected', `${ip} # ${oldMarker}`, 0, 1],
  ['wrong-scope', `${ip} # ${marker} unrelated; ${oldMarker} unrelated`, 1, 1],
  ['short-scope', `${ip} # ${marker} 23; ${oldMarker} 23`, 0, 1],
  ['reverse-scope', `${ip} # ${marker} ${ip}-extra; ${oldMarker} ${ip}-extra`, 0, 1],
  ['other-hit-on-line', `${scoped(ip)} ${email}`, 1, 1],
];
let failed = 0;
const root = mkdtempSync(join(tmpdir(), 'structural-controls-'));
try {
  mkdirSync(join(root, 'scripts'));
  copyFileSync('scripts/secrets-scan.mjs', join(root, 'scripts', 'secrets-scan.mjs'));
  for (const args of [['init', '-q'], ['add', 'scripts/secrets-scan.mjs']]) {
    if (run('git', args, root).status !== 0) throw new Error('fixture Git setup failed');
  }
  for (const [id, content, legacyExit, nativeExit] of cases) {
    writeFileSync(join(root, 'fixture.txt'), content + '\n');
    if (run('git', ['add', 'fixture.txt'], root).status !== 0) throw new Error('fixture stage failed');
    const summaries = scanPair(root, config);
    const ok = summaries.every((summary, index) => summary.valid
      && summary.exit === [legacyExit, nativeExit][index]
      && (summary.exit === 0 ? summary.hits === 0 : summary.hits > 0));
    console.log(JSON.stringify({ control: id, passed: ok, results: summaries }));
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
  rmSync(root, { recursive: true, force: true });
}
console.log('controls_failed=' + failed);
process.exitCode = failed === 0 ? 0 : 1;

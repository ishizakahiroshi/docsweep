import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync, writeFileSync, chmodSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { inspect, isolatedEnv, nativeArgs, packagePin, passed, scanPair, validateConfig, nativeInvocation, safeFindings, failureReason, matchesExpectedSha, resolveWindowsTools, runtimePreflight, exactNativeVersion, provisionPublicRuntime } from './structural-ci.mjs';
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
    for (const flag of ['--strict', '--block', '--all-tracked', '--format=json', '--config', '/public-config.json']) assert.ok(/(?:^|[\\/])cmd\.exe$/i.test(calls[1].cmd) ? calls[1].args[3].includes('"' + flag + '"') : calls[1].args.includes(flag));
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

test('malformed counter strings never reach public summaries', () => {
  const sentinel = 'SYNTHETIC_NOT_FOR_LOGS';
  for (const [kind, fields] of [['legacy', ['kb_needles', 'family_needles']], ['doxguard', ['watchlist_needles', 'coverage_skips']]]) {
    for (const field of fields) {
      const summary = inspect({status: 0, stdout: JSON.stringify({...report(kind), [field]: sentinel})}, kind);
      assert.equal(passed(summary), false);
      assert.ok(!JSON.stringify(summary).includes(sentinel));
    }
  }
});

test('finding metadata is actionable but matching values in filenames and sources are redacted', () => {
  const needle = ['10', '23', '45', '67'].join('.');
  const hits = [
    {matched: needle, file: needle + '.txt', lineNumber: 12, kind:'structural', source:'structural: Private IPv4 (RFC1918)'},
    {matched:'Windows', file:'normal.txt', lineNumber:7, kind:'structural', source:'structural: Windows absolute path'},
  ];
  const details = safeFindings(hits);
  assert.equal(details[0].file, '[redacted metadata]');
  assert.equal(details[0].line, 12);
  assert.equal(details[0].source, 'structural: Private IPv4 (RFC1918)');
  assert.equal(details[1].file, 'normal.txt');
  assert.equal(details[1].source, '[redacted metadata]');
  assert.ok(!JSON.stringify(details).includes(needle));
});
test('Windows npm uses cmd explicitly and rejects expansion or injection characters', () => {
  const options = {env:{SystemRoot:'C:\\Windows',Path:'C:\\Program Files\\nodejs'},cwd:'C:\\fixture',isFile:() => true};
  const call = nativeInvocation(['scan','--config','C:/fixture folder/public.json'], 'win32', options);
  assert.equal(call.command,'C:\\Windows\\System32\\cmd.exe');
  assert.deepEqual(call.args.slice(0,3),['/d','/s','/c']);
  assert.ok(call.args[3].startsWith('""C:\\Program Files\\nodejs\\npm.cmd" '));
  assert.ok(call.args[3].includes('"C:/fixture folder/public.json"'));
  for (const char of ['%', '!', '&', '|', '<', '>', '^', '"', '\n', '\r', '\0']) assert.throws(() => nativeInvocation(['scan',char], 'win32', options));
  assert.equal(nativeInvocation(['--version'],'linux').command,'npm');
});
test('Windows resolves system cmd and PATH npm without current-directory fallback', () => {
  const probes = [];
  const options = {
    env:{systemroot:'C:\\Windows',pAtH:';.;relative;C:\\fixture;C:\\absent;"C:\\Trusted Node"'},
    cwd:'c:\\FIXTURE',
    isFile:path => {
      probes.push(path);
      return ['C:\\Windows\\System32\\cmd.exe','C:\\Trusted Node\\npm.cmd'].includes(path);
    },
  };
  assert.deepEqual(resolveWindowsTools(options),{command:'C:\\Windows\\System32\\cmd.exe',npm:'C:\\Trusted Node\\npm.cmd'});
  assert.deepEqual(probes,['C:\\Windows\\System32\\cmd.exe','C:\\absent\\npm.cmd','C:\\Trusted Node\\npm.cmd']);
  for (const env of [{Path:'C:\\Trusted Node'}, {SystemRoot:'relative',Path:'C:\\Trusted Node'}, {SystemRoot:'C:\\Windows',Path:'.;C:\\fixture'}]) {
    assert.throws(() => resolveWindowsTools({...options,env}));
  }
  assert.throws(() => resolveWindowsTools({...options,isFile:() => false}));
});
test('npm diagnostics are fixed categories, never raw error text', () => {
  assert.equal(failureReason({stderr:'npm error code ETARGET hidden text'}),'package-unavailable');
  assert.equal(failureReason({stderr:'npm error code ENOTFOUND hidden text'}),'package-network');
});

test('expected checkout SHA and case-insensitive environment isolation fail closed', () => {
  const sha = 'a'.repeat(40);
  assert.equal(matchesExpectedSha(sha,sha,true),true);
  assert.equal(matchesExpectedSha(sha,'b'.repeat(40),true),false);
  assert.equal(matchesExpectedSha(sha,undefined,true),false);
  assert.equal(matchesExpectedSha('invalid',sha,false),false);
  assert.deepEqual(isolatedEnv({kb_root:'synthetic',Family_Root:'synthetic',doxguard_config:'synthetic',Path:'keep'}),{Path:'keep'});
});
test('preflight classifies execution errors without raw diagnostics', () => {
  for (const [input, expected] of [[{error:{code:'ENOENT'}},'executable-or-interpreter-missing'],[{error:{code:'EACCES'}},'executable-permission'],[{stderr:'sh: 1: doxguard: not found'},'command-not-found'],[{stderr:'error while loading shared libraries: hidden'},'native-loader']]) assert.equal(failureReason(input),expected);
  assert.equal(exactNativeVersion({status:0,stdout:'doxguard 0.2.0\n'}),true);
  assert.equal(exactNativeVersion({status:127,stdout:'doxguard 0.2.0'}),false);
});
test('preflight distinguishes direct and launcher success from npm exec failure', () => {
  const calls=[];
  const receipt=runtimePreflight({platform:'linux',arch:'x64',execute:(command,args,cwd,timeout)=>{
    assert.ok(timeout > 0 && timeout <= 180_000);
    calls.push({command,args});
    if(args[0]==='install') {
      const root=join(cwd,'node_modules','doxguard');
      const platform=join(cwd,'node_modules','@ishizakahiroshi','doxguard-linux-x64');
      mkdirSync(join(root,'bin'),{recursive:true});mkdirSync(join(platform,'bin'),{recursive:true});
      writeFileSync(join(root,'package.json'),JSON.stringify({name:'doxguard',version:'0.2.0'}));
      writeFileSync(join(platform,'package.json'),JSON.stringify({name:'@ishizakahiroshi/doxguard-linux-x64',version:'0.2.0'}));
      writeFileSync(join(platform,'bin','doxguard'),'synthetic-not-executed');chmodSync(join(platform,'bin','doxguard'),0o755);
      return {status:0,stdout:'',stderr:''};
    }
    if(command==='npm' && args[0]==='--version')return {status:0,stdout:'10.9.0'};
    if(args[0]==='exec')return {status:127,stderr:'sh: 1: doxguard: not found HIDDEN_SENTINEL'};
    return {status:0,stdout:'doxguard 0.2.0\n'};
  }});
  assert.equal(receipt.direct,'version-ok');assert.equal(receipt.launcher,'version-ok');
  assert.equal(receipt.npm_exec,'command-not-found');assert.equal(receipt.accepted,true);
  assert.equal(receipt.selected_route,'verified-root-launcher');
  assert.match(receipt.payload_sha256,/^[a-f0-9]{64}$/);
  assert.ok(!JSON.stringify(receipt).includes('HIDDEN_SENTINEL'));
  assert.ok(calls.find(c=>c.args[0]==='install').args.includes('--ignore-scripts'));
});
test('preflight missing package and unsupported platforms never fabricate runtime acceptance', () => {
  assert.equal(runtimePreflight({platform:'darwin'}).accepted,false);
  const result=runtimePreflight({platform:'linux',arch:'x64',execute:()=>({status:1,stderr:'npm error E404 HIDDEN_SENTINEL'})});
  assert.equal(result.reason,'package-unavailable');assert.equal(result.accepted,false);
  assert.ok(!JSON.stringify(result).includes('HIDDEN_SENTINEL'));
});

test('exact public runtime provisioning preserves failure and rejects missing entrypoints', () => {
  const a=provisionPublicRuntime(()=>({status:127,stderr:'SENTINEL'}),'x64');
  try { assert.equal(a.ready,false);assert.equal(a.failure.status,127); } finally {rmSync(a.root,{recursive:true,force:true});}
  const b=provisionPublicRuntime(()=>({status:0}),'x64');
  try {assert.equal(b.ready,false);assert.equal(b.failure.error.code,'PUBLIC_RUNTIME_SETUP');} finally {rmSync(b.root,{recursive:true,force:true});}
  assert.equal(failureReason({stderr:'doxguard: GLIBC_2.39 not found'}),'native-loader');
});
test('exact public runtime selects the verified package launcher rather than a PATH command', () => {
  const runtime=provisionPublicRuntime((command,args,cwd)=>{
    assert.equal(command,'npm');assert.ok(args.includes('doxguard@0.2.0'));assert.ok(args.includes('--ignore-scripts'));
    const root=join(cwd,'node_modules','doxguard');const platform=join(cwd,'node_modules','@ishizakahiroshi','doxguard-linux-x64');
    for(const dir of [root,platform])mkdirSync(join(dir,'bin'),{recursive:true});
    writeFileSync(join(root,'package.json'),JSON.stringify({name:'doxguard',version:'0.2.0'}));
    writeFileSync(join(platform,'package.json'),JSON.stringify({name:'@ishizakahiroshi/doxguard-linux-x64',version:'0.2.0'}));
    writeFileSync(join(root,'bin','doxguard.js'),'synthetic-not-executed');writeFileSync(join(platform,'bin','doxguard'),'synthetic-not-executed');
    return {status:0};
  },'x64');
  try {assert.equal(runtime.ready,true);assert.equal(runtime.launcher,join(runtime.root,'node_modules','doxguard','bin','doxguard.js'));}
  finally {rmSync(runtime.root,{recursive:true,force:true});}
});

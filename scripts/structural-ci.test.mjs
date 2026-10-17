import test from 'node:test';
import assert from 'node:assert/strict';
import { statSync, symlinkSync, unlinkSync, mkdtempSync, readFileSync, mkdirSync, writeFileSync, chmodSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { createHash } from 'node:crypto';
import { join } from 'node:path';
import { inspect, isolatedEnv, nativeArgs, packagePin, passed, scanPair, validateConfig, nativeInvocation, safeFindings, failureReason, matchesExpectedSha, resolveWindowsTools, runtimePreflight, exactNativeVersion, provisionPublicRuntime, verifyRuntime, canonicalRuntime } from './structural-ci.mjs';
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
test('preflight shares the supplied runtime and fails closed on invalid payload', () => {
  let calls=0;
  const receipt=runtimePreflight({platform:'linux',arch:'x64',runtime:()=>({ready:false,failure:{status:127,stderr:'doxguard: not found'}}),execute:()=>{calls++;throw Error('must not execute');}});
  assert.equal(receipt.reason,'command-not-found');assert.equal(receipt.accepted,false);assert.equal(calls,0);
  assert.equal(runtimePreflight({platform:'darwin'}).accepted,false);
  assert.equal(runtimePreflight({platform:'linux',arch:'arm64'}).accepted,false);
});
test('exact public runtime provisioning preserves failure and rejects missing entrypoints', () => {
  const a=provisionPublicRuntime(()=>({status:127,stderr:'SENTINEL'}),'x64');
  try { assert.equal(a.ready,false);assert.equal(a.failure.status,127); } finally {rmSync(a.root,{recursive:true,force:true});}
  const b=provisionPublicRuntime(()=>({status:0}),'x64');
  try {assert.equal(b.ready,false);assert.equal(b.failure.error.code,'PUBLIC_RUNTIME_SETUP');} finally {rmSync(b.root,{recursive:true,force:true});}
  assert.equal(failureReason({stderr:'doxguard: GLIBC_2.39 not found'}),'native-loader');
});
// Synthetic Linux mode is explicit: Windows chmod does not emulate POSIX
// execute bits. Actual production calls always use the default real statSync.
const syntheticExecutableStat = path => ({isFile:()=>statSync(path).isFile(),mode:0o755});
function syntheticRuntime() {
  const root=mkdtempSync(join(tmpdir(),'runtime-unit-'));
  const a=join(root,'node_modules','doxguard');
  const b=join(root,'node_modules','@ishizakahiroshi','doxguard-linux-x64');
  for(const dir of [a,b])mkdirSync(join(dir,'bin'),{recursive:true});
  const rootMeta={name:'doxguard',version:'0.2.0',type:'module',bin:{doxguard:'bin/doxguard.js'},optionalDependencies:{'@ishizakahiroshi/doxguard-linux-x64':'0.2.0'}};
  const platformMeta={name:'@ishizakahiroshi/doxguard-linux-x64',version:'0.2.0',bin:{doxguard:'bin/doxguard'},os:['linux'],cpu:['x64']};
  const reset=()=>{writeFileSync(join(a,'package.json'),JSON.stringify(rootMeta));writeFileSync(join(b,'package.json'),JSON.stringify(platformMeta));writeFileSync(join(a,'bin','doxguard.js'),'synthetic-launcher');writeFileSync(join(b,'bin','doxguard'),'synthetic-native');chmodSync(join(b,'bin','doxguard'),0o755);};
  reset();
  const hash=value=>createHash('sha256').update(value).digest('hex');
  return {root,a,b,rootMeta,platformMeta,reset,expected:{arch:'x64',payload:hash('synthetic-native'),launcher:hash('synthetic-launcher')}};
}
test('runtime identity matches exact root/platform relationship and both hashes',()=>{
  const f=syntheticRuntime();
  try {
    const identity=verifyRuntime(f.root,'x64',f.expected,syntheticExecutableStat);
    assert.equal(identity.receipt.canonical_match,true);
    assert.equal(identity.receipt.payload_sha256,f.expected.payload);
    assert.equal(identity.receipt.launcher_sha256,f.expected.launcher);
    assert.throws(()=>verifyRuntime(f.root,'x64',canonicalRuntime,syntheticExecutableStat), /canonical hash mismatch/); // synthetic bytes are never accepted as public release
    assert.throws(()=>verifyRuntime(f.root,'arm64',f.expected,syntheticExecutableStat));
    assert.equal(canonicalRuntime.payload,'5458f08f85753cf6d33ad869063af16ca8f55a800bb9d17a78c4b6adc0651279');
  } finally {rmSync(f.root,{recursive:true,force:true});}
});
test('runtime rejects wrong optional pin, bin, module type and platform metadata',()=>{
  const f=syntheticRuntime();
  try {
    for(const change of [{version:'0.2.1'},{name:'other'},{type:'commonjs'},{bin:{doxguard:'other.js'}},{optionalDependencies:{}},{optionalDependencies:{'@ishizakahiroshi/doxguard-linux-x64':'^0.2.0'}}]) {
      f.reset();writeFileSync(join(f.a,'package.json'),JSON.stringify({...f.rootMeta,...change}));assert.throws(()=>verifyRuntime(f.root,'x64',f.expected,syntheticExecutableStat));
    }
    for(const change of [{name:'other'},{version:'0.2.1'},{bin:{doxguard:'elsewhere'}},{os:['darwin']},{os:['linux','darwin']},{cpu:['arm64']},{cpu:['x64','arm64']}]) {
      f.reset();writeFileSync(join(f.b,'package.json'),JSON.stringify({...f.platformMeta,...change}));assert.throws(()=>verifyRuntime(f.root,'x64',f.expected,syntheticExecutableStat));
    }
  } finally {rmSync(f.root,{recursive:true,force:true});}
});
test('runtime rejects either altered payload and nonexecutable native before invocation',()=>{
  const f=syntheticRuntime();
  try {
    for(const path of [join(f.a,'bin','doxguard.js'),join(f.b,'bin','doxguard')]) {
      f.reset();writeFileSync(path,'different-bytes');assert.throws(()=>verifyRuntime(f.root,'x64',f.expected,syntheticExecutableStat));
    }
    f.reset();
    assert.doesNotThrow(()=>verifyRuntime(f.root,'x64',f.expected,syntheticExecutableStat));
    const nonExecutableStat=path=>({...syntheticExecutableStat(path),mode:0o644});
    assert.throws(()=>verifyRuntime(f.root,'x64',f.expected,nonExecutableStat), /entrypoint mismatch/);
  } finally {rmSync(f.root,{recursive:true,force:true});}
});
test('runtime rejects nested platform package resolution despite matching outer package',()=>{
  const f=syntheticRuntime();
  try {
    const nested=join(f.a,'node_modules','@ishizakahiroshi','doxguard-linux-x64');mkdirSync(nested,{recursive:true});writeFileSync(join(nested,'package.json'),JSON.stringify(f.platformMeta));
    assert.throws(()=>verifyRuntime(f.root,'x64',f.expected,syntheticExecutableStat));
  } finally {rmSync(f.root,{recursive:true,force:true});}
});
test('preflight never invokes unverified synthetic runtime or leaks paths',()=>{
  const f=syntheticRuntime();let calls=0;
  try {
    const receipt=runtimePreflight({runtime:()=>({ready:true,root:f.root}),platform:'linux',arch:'x64',execute:()=>{calls++;}});
    assert.equal(receipt.accepted,false);assert.equal(receipt.reason,'runtime-identity-or-payload-mismatch');assert.equal(calls,0);assert.ok(!JSON.stringify(receipt).includes(f.root));
  } finally {rmSync(f.root,{recursive:true,force:true});}
});

test('runtime rejects symlinked launcher and payload despite matching bytes',()=>{
  const f=syntheticRuntime();
  try {
    for(const [path,bytes] of [[join(f.a,'bin','doxguard.js'),'synthetic-launcher'],[join(f.b,'bin','doxguard'),'synthetic-native']]) {
      const target=join(f.root,'redirected');writeFileSync(target,bytes);chmodSync(target,0o755);unlinkSync(path);symlinkSync(target,path);
      assert.throws(()=>verifyRuntime(f.root,'x64',f.expected,syntheticExecutableStat));unlinkSync(path);f.reset();
    }
  } finally {rmSync(f.root,{recursive:true,force:true});}
});

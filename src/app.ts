/// <reference types="htmx.org" />

// docSweep Web UI — htmx + TypeScript 版
// CSP（script-src 'self'）下で動くよう inline onclick は使わず、
// data-action 属性 + document への単一イベント委譲で配線する（動的挿入要素にも効く）。

function token(): string {
  return (document.body as HTMLElement).dataset.token ?? '';
}

function post(url: string, data: Record<string, string>): Promise<Response> {
  const body = new URLSearchParams({ token: token(), ...data });
  return fetch(url, { method: 'POST', body });
}

function refreshContent(): void {
  htmx.ajax('GET', `/fragment?token=${encodeURIComponent(token())}`, {
    target: '#page-content',
    swap: 'innerHTML',
  });
}

function actionLabel(a: string): string {
  return ({
    discard: 'archive へ移送',
    promote: '完了にして archive へ',
    relabel: '保留にする',
    resume: '再開する',
    keep: '維持',
  } as Record<string, string>)[a] ?? a;
}

async function act(path: string, action: string, to?: string): Promise<void> {
  const data: Record<string, string> = { path, action };
  if (action === 'relabel' && to) data.to = to;
  if (!confirm(`${actionLabel(action)}：\n${path.split('/').pop()}\n\nよろしいですか？`)) return;
  const r = await post('/api/apply', data);
  if (!r.ok) { alert('失敗: ' + await r.text()); return; }
  refreshContent();
}

async function openf(path: string): Promise<void> {
  const r = await post('/api/open', { path });
  if (!r.ok) alert('開けませんでした: ' + await r.text());
}

async function revealf(path: string): Promise<void> {
  const r = await post('/api/reveal', { path });
  if (!r.ok) alert('フォルダを開けませんでした: ' + await r.text());
}

async function sweep(): Promise<void> {
  if (!confirm('完了 / 廃止 のファイルを各プロジェクトの archive/ へ移送します。\n（様子見は触りません）')) return;
  const r = await post('/api/sweep', { dry_run: 'false' });
  if (!r.ok) { alert('失敗: ' + await r.text()); return; }
  const moved = (await r.json()) as unknown[];
  alert(`${moved.length} 件を archive へ移送しました。`);
  refreshContent();
}

async function archiveAll(): Promise<void> {
  const cards = [...document.querySelectorAll<HTMLElement>('.qcard[data-arch]')];
  if (!cards.length) return;
  if (!confirm(`表示中の ${cards.length} 件を archive へ移送します。\nよろしいですか？`)) return;
  let ok = 0, failed = 0;
  for (const c of cards) {
    const r = await post('/api/apply', {
      path: c.dataset.path ?? '',
      action: c.dataset.arch ?? '',
    });
    r.ok ? ok++ : failed++;
  }
  if (failed) {
    alert(`${ok} 件を archive へ移送、${failed} 件は移送できませんでした（要修正のファイルが残っています）。`);
  }
  refreshContent();
}

function showModal(): void {
  document.getElementById('modal')!.classList.remove('hidden');
  document.getElementById('modal-body')!.innerHTML = '<div class="empty">読み込み中…</div>';
}

function closeModal(): void {
  document.getElementById('modal')!.classList.add('hidden');
}

// ---- inject / eject（プレビュー必須 → 確認 → 実行）----

function mk(tag: string, cls?: string, text?: string | null): HTMLElement {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function presetVal(): string {
  const s = document.getElementById('inj-preset') as HTMLSelectElement | null;
  return s ? s.value : '';
}

async function injectPreview(
  op: string, scope: string, project?: string, agent?: string,
): Promise<void> {
  const url = op === 'inject' ? '/api/inject' : '/api/eject';
  const data: Record<string, string> = { scope, dry_run: 'true' };
  if (scope === 'project') {
    if (project) data.project = project;
    data.preset = presetVal();
  } else {
    if (agent) data.agent = agent;
  }
  const r = await post(url, data);
  if (!r.ok) { alert('プレビュー失敗: ' + await r.text()); return; }
  renderInjectPreview(await r.json() as Record<string, unknown>, op, scope, project, agent);
}

function renderInjectPreview(
  pv: Record<string, unknown>,
  op: string,
  scope: string,
  project?: string,
  agent?: string,
): void {
  const body = document.getElementById('modal-body')!;
  body.innerHTML = '';
  const opLabel = op === 'inject' ? '注入' : '解除';
  body.appendChild(mk('h2', 'inj-h', `🔧 ${opLabel}プレビュー`));
  body.appendChild(mk('div', 'inj-target', pv.path as string));
  if (scope === 'global') {
    body.appendChild(mk('div', 'inj-warnbox',
      '⚠ これは個人グローバル設定への書き込みです。全プロジェクトのセッションに影響します。'));
  }
  ((pv.warnings as string[]) || []).forEach((msg) =>
    body.appendChild(mk('div', 'inj-warnbox', `⚠ ${msg}`)));

  if (op === 'inject') {
    ((pv.blocks as Array<{ file: string; text: string }>) || []).forEach((b) => {
      body.appendChild(mk('div', 'inj-file', `▶ ${b.file} に追記:`));
      body.appendChild(mk('pre', 'inj-pre', b.text));
    });
    if (pv.scope === 'global' && pv.guidance) {
      body.appendChild(mk('div', 'inj-file', `▶ ${pv.guidance_path as string}（docSweep 所有・自動生成）:`));
      body.appendChild(mk('pre', 'inj-pre', pv.guidance as string));
    }
    if (pv.scope === 'project') {
      body.appendChild(mk('div', 'inj-note',
        pv.yaml_exists
          ? '.docSweep.yaml は既存（温存）'
          : '.docSweep.yaml を新規作成します'));
    }
  } else {
    const removed = (pv.removed as string[]) || [];
    body.appendChild(mk('div', 'inj-note', removed.length
      ? `次のファイルから docSweep 管理ブロックを除去します: ${removed.join(', ')}`
      : '除去対象の管理ブロックは見つかりませんでした。'));
    if (scope === 'project') {
      const lab = mk('label', 'inj-purge-lab') as HTMLLabelElement;
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.id = 'inj-purge';
      lab.appendChild(cb);
      lab.appendChild(document.createTextNode(' .docSweep.yaml も削除する'));
      body.appendChild(lab);
    }
  }

  const apply = mk('button', 'btn danger', `この内容で${opLabel}を実行`) as HTMLButtonElement;
  apply.dataset.action = 'inject-apply';
  apply.dataset.op = op;
  apply.dataset.scope = scope;
  if (project) apply.dataset.project = project;
  if (agent) apply.dataset.agent = agent;
  body.appendChild(apply);

  document.getElementById('modal')!.classList.remove('hidden');
}

async function injectApply(
  op: string, scope: string, project?: string, agent?: string,
): Promise<void> {
  const url = op === 'inject' ? '/api/inject' : '/api/eject';
  const data: Record<string, string> = { scope, dry_run: 'false' };
  if (scope === 'project') {
    if (project) data.project = project;
    if (op === 'inject') data.preset = presetVal();
    const purgeEl = document.getElementById('inj-purge') as HTMLInputElement | null;
    if (op === 'eject') data.purge = purgeEl?.checked ? 'true' : 'false';
  } else {
    if (agent) data.agent = agent;
  }
  const r = await post(url, data);
  if (!r.ok) { alert('失敗: ' + await r.text()); return; }
  refreshContent();
}

// ---- イベント配線 ----

document.addEventListener('keydown', (e: KeyboardEvent) => {
  if (e.key === 'Escape') closeModal();
});

// htmx:responseError でモーダルの読み込み失敗を表示
document.addEventListener('htmx:responseError', (e: Event) => {
  const target = (e as CustomEvent).detail?.target as Element | undefined;
  if (target?.id === 'modal-body') {
    target.innerHTML = '<div class="empty">読み込みに失敗しました。</div>';
  }
});

document.addEventListener('click', (e: MouseEvent) => {
  // モーダル背景（#modal 自身）クリックで閉じる
  if ((e.target as Element).id === 'modal') { closeModal(); return; }
  const el = (e.target as Element).closest<HTMLElement>('[data-action]');
  if (!el) return;
  const p = el.dataset.path ?? '';
  switch (el.dataset.action) {
    case 'discard':
    case 'promote':
      void act(p, el.dataset.action);
      break;
    case 'relabel':
      void act(p, 'relabel', el.dataset.to);
      break;
    case 'detail': {
      showModal();
      const previewUrl = `/preview?token=${encodeURIComponent(token())}&path=${encodeURIComponent(p)}`;
      void htmx.ajax('GET', previewUrl, { target: '#modal-body', swap: 'innerHTML' });
      break;
    }
    case 'open':
      void openf(p);
      break;
    case 'reveal':
      void revealf(p);
      break;
    case 'sweep':
      void sweep();
      break;
    case 'archive-all':
      void archiveAll();
      break;
    case 'inject-preview':
      void injectPreview('inject', el.dataset.scope ?? '', el.dataset.project, el.dataset.agent);
      break;
    case 'eject-preview':
      void injectPreview('eject', el.dataset.scope ?? '', el.dataset.project, el.dataset.agent);
      break;
    case 'inject-apply':
      void injectApply(el.dataset.op ?? '', el.dataset.scope ?? '', el.dataset.project, el.dataset.agent);
      break;
    case 'toggle-fold': {
      const f = el.closest('.fold');
      if (f) f.classList.toggle('open');
      break;
    }
    case 'open-archivable': {
      const x = document.getElementById('archivable');
      if (x) x.classList.add('open');
      break;
    }
    case 'toggle-all-open':
      document.querySelectorAll<HTMLDetailsElement>('.proj-accordion').forEach(d => { d.open = true; });
      break;
    case 'toggle-all-close':
      document.querySelectorAll<HTMLDetailsElement>('.proj-accordion').forEach(d => { d.open = false; });
      break;
    case 'close-modal':
      closeModal();
      break;
    case 'view': {
      const view = el.dataset.view;
      if (!view) break;
      document.querySelectorAll<HTMLElement>('section.view').forEach(sec => {
        sec.classList.toggle('hidden', sec.dataset.view !== view);
      });
      document.querySelectorAll<HTMLElement>('.nav[data-primary]').forEach(nav => {
        nav.classList.toggle('active', nav.dataset.view === view);
      });
      const titles: Record<string, [string, string]> = {
        dashboard: ['ダッシュボード', '今日捌くべき判断'],
        settings: ['設定', '運用ルールの注入・管理'],
      };
      const [t, s] = titles[view] ?? [view, ''];
      const titleEl = document.getElementById('view-title');
      const subEl = document.getElementById('view-sub');
      if (titleEl) titleEl.textContent = t;
      if (subEl) subEl.textContent = s;
      break;
    }
  }
});

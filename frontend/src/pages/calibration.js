import {CALIBRATION_EVENT_LABELS, CHANNELS, MACHINES, TASK_KINDS, VERDICTS} from '../labels.js';
import {empty, heading, lotLink, number, options, safe, sectionTitle, status, time} from '../ui.js';

const CHANNEL_NAMES = Object.fromEntries(CHANNELS.map(([key, label, unit]) => [key, `${label}（${unit}）`]));

function correctionFields(corrections = {}) {
  return CHANNELS.map(([key, label, unit]) => `<label><span>${label} <small class="unit">${unit} · 修正量</small></span><input name="${key}" aria-label="${label}修正量" type="number" step="any" value="${safe(corrections[key] ?? '')}" placeholder="0"></label>`).join('');
}

function correctionsTable(corrections) {
  const entries = Object.entries(corrections || {});
  if (!entries.length) return '<p class="muted">本勘误未登记修正量。</p>';
  return `<div class="channel-list">${entries.map(([channel, delta]) => `<div class="channel-row"><span>${safe(CHANNEL_NAMES[channel] || channel)}</span><b>${delta > 0 ? '+' : ''}${number(delta)}</b></div>`).join('')}</div>`;
}

function registerDialog() {
  return `<dialog id="calibration-register"><div class="dialog-heading"><h2>登记机台校准勘误</h2><button data-dismiss="calibration-register" aria-label="关闭">×</button></div>
    <p>按签核样本的采样时间圈定已签批次，用原模型对修正后读数重算。原读数与签核保留可查。</p>
    <form class="fields" data-form="calibration-register"><label>机台<select name="machine">${options(Object.fromEntries(MACHINES.map(machine => [machine, machine])))}</select></label><label>受影响时段开始<input type="datetime-local" name="window_start" required></label><label>受影响时段结束<input type="datetime-local" name="window_end" required></label><label>勘误说明<input name="note" required minlength="2" maxlength="300" placeholder="例如：校准片偏位基准偏移"></label><div class="fields full">${correctionFields()}</div><button class="primary full">受理勘误并重算</button></form></dialog>`;
}

function calibrationRow(c, selected) {
  return `<tr class="${c.id === selected ? 'selected' : ''}"><td><b class="mono">CL-${String(c.id).padStart(4, '0')}</b><small>v${c.revision} · ${safe(c.created_by)}</small></td><td class="mono">${safe(c.machine)}</td><td><small>${time(c.window_start)} → ${time(c.window_end)}</small></td><td>${status(c.state)}</td><td class="mono">${c.scoped}</td><td class="mono">${c.lower_count}</td><td>${c.pending_holds ? `<b class="warn-text">${c.pending_holds}</b>` : '0'} / ${c.pending_investigations}</td><td><a data-nav href="/calibrations?calibration=${c.id}">查看 →</a></td></tr>`;
}

function taskPanel(data, account) {
  const {calibration, tasks} = data;
  const pending = tasks.filter(task => task.state === 'pending');
  if (!tasks.length) return `<section class="panel">${sectionTitle('处置进度', '关批与追查')}<p class="muted">本次重算没有产生关批拦截或已关批追查待办。</p></section>`;
  return `<section class="panel">${sectionTitle('处置进度', `${pending.length} 项待处置`)}<div class="task-list">${tasks.map(task => `<article class="task ${safe(task.state)}"><div><b>${TASK_KINDS[task.kind] || task.kind}</b> <small class="mono">${safe(task.code)}</small>${status(task.state)}</div><small>${task.state === 'cleared' ? `${safe(task.cleared_by)} · ${safe(task.clear_note)} · ${time(task.cleared_at)}` : `重算 ${safe(task.detail?.recomputed_grade || '—')} 级 · 勘误 v${task.revision}`}</small>${task.state === 'pending' && account.role === 'supervisor' ? `<form data-form="clear-task" data-calibration="${calibration.id}" data-task="${task.id}" class="mark-review"><label>处置说明<input name="note" required minlength="2" maxlength="300" placeholder="复检结论 / 追查记录"></label><button class="primary">签核</button></form>` : ''}</article>`).join('')}</div></section>`;
}

function resultsTable(data, selectedLot) {
  const results = new Map(data.results.map(result => [result.lot_id, result]));
  const scopes = data.scopes;
  if (!scopes.length) return empty('受影响时段内没有已签批次', '据签核样本的采样时间未圈定到批次。后续签核的样本会在落入时段时补入。');
  return `<div class="scroll-x"><table><thead><tr><th>包装批次</th><th>样本 / 采样时间</th><th>原模型</th><th>人工签核</th><th>重算等级</th><th>对照</th><th>当前工序</th></tr></thead><tbody>${scopes.map(scope => {
    const result = results.get(scope.lot_id);
    const verdict = result?.verdict;
    return `<tr class="${scope.lot_id === selectedLot ? 'selected' : ''}"><td><a data-nav href="/calibrations?calibration=${data.calibration.id}&lot=${scope.lot_id}" aria-label="对照 ${safe(scope.code)} 读数"><b class="mono">${safe(scope.code)}</b></a><small>${safe(scope.product)}</small></td><td>${safe(scope.sample)} <small>${time(scope.sampled_at)}</small></td><td><b class="grade-inline">${safe(scope.prior_model_grade)}</b></td><td><b class="grade-inline">${safe(scope.prior_final_grade)}</b><small>${safe(scope.prior_reviewed_by)} · ${time(scope.prior_reviewed_at)}</small></td><td>${result?.state === 'error' ? `<small class="warn-text">${safe(result.failure)}</small>` : `<b class="grade-inline">${safe(result?.recomputed_grade || '—')}</b>`}</td><td>${verdict ? `<span class="verdict ${safe(verdict)}">${safe(VERDICTS[verdict])}</span>` : '—'}</td><td>${status(scope.lot_state)}</td></tr>`;
  }).join('')}</tbody></table></div>`;
}

function readingsCompare(scope, result) {
  if (!scope) return '';
  return `<div class="compare-grid"><div><h4>原读数</h4>${CHANNELS.map(([key, label, unit]) => `<div class="channel-row"><span>${label}</span><b>${scope.original_readings?.[key] == null ? '缺失' : `${number(scope.original_readings[key])} <small>${unit}</small>`}</b></div>`).join('')}</div><div><h4>修正后读数</h4>${CHANNELS.map(([key, label, unit]) => `<div class="channel-row"><span>${label}</span><b>${result?.corrected_readings?.[key] == null ? '—' : `${number(result.corrected_readings[key])} <small>${unit}</small>`}</b></div>`).join('')}</div></div>`;
}

export function calibrationsPage(lots, calibrations, data, account, selectedId, selectedLot = 0) {
  const calibration = data?.calibration;
  const scope = calibration ? (data.scopes.find(item => item.lot_id === selectedLot) || data.scopes[0]) : null;
  const result = calibration ? (data.results.find(item => item.lot_id === scope?.lot_id) || data.results[0]) : null;
  return `${heading('包装线 / 机台校准', '校准勘误', '登记机台偏差，按采样时间圈定已签批次并用原模型重算，对照原判与处置进度。', account.role === 'supervisor' ? '<button class="primary" data-dialog="calibration-register">＋ 登记勘误</button>' : '')}
    <section class="panel ledger">${sectionTitle('勘误台账', `${calibrations.length} 条`)}${calibrations.length ? `<div class="scroll-x"><table><thead><tr><th>勘误</th><th>机台</th><th>受影响时段</th><th>重算状态</th><th>圈定批次</th><th>低于原判</th><th>待拦截 / 追查</th><th></th></tr></thead><tbody>${calibrations.map(item => calibrationRow(item, selectedId)).join('')}</tbody></table></div>` : empty('尚无校准勘误','机台校准发现偏差后，由质检主管登记受影响时段与各通道修正量。')}</section>
    ${!calibration ? '' : `<div class="calibration-grid"><div class="stack">
      <section class="panel">${sectionTitle(`CL-${String(calibration.id).padStart(4, '0')} · 勘误详情`, `v${calibration.revision} · ${calibration.state === 'computed' ? '已重算' : '重算中'}`)}
        <div class="lot-strip"><div><small>机台</small><b>${safe(calibration.machine)}</b></div><div><small>受影响时段</small><b>${time(calibration.window_start)} → ${time(calibration.window_end)}</b></div><div><small>受理</small><b>${safe(calibration.created_by)} · ${time(calibration.created_at)}</b></div>${status(calibration.state)}</div>
        <p class="notice">${safe(calibration.note)}</p>${correctionsTable(calibration.corrections)}
        ${calibration.state === 'computed' ? (account.role === 'supervisor' ? `<div class="button-line"><button data-dialog="calibration-amend">更正修正量</button><button data-action="calibration-retry" data-calibration="${calibration.id}">重新重算</button></div>` : '') : '<p class="muted">重算完成后结果与处置待办会在此更新。</p>'}
      </section>
      <section class="panel">${sectionTitle('重算结果', '原模型对照')}${resultsTable(data, scope?.lot_id)}</section>
      <section class="panel">${sectionTitle('读数对照', scope?.sample || '')}${readingsCompare(scope, result)}</section>
    </div><div class="stack">
      ${taskPanel(data, account)}
      <section class="panel">${sectionTitle('勘误履历')}<div class="timeline">${data.events.map(event => `<article><i></i><div><strong>${safe(CALIBRATION_EVENT_LABELS[event.action] || event.action)}</strong><small>${safe(event.actor)} · ${time(event.at)}</small>${event.details?.note ? `<p>${safe(event.details.note)}</p>` : ''}${event.details?.corrections ? `<p>${Object.entries(event.details.corrections).map(([channel, delta]) => `${safe(CHANNEL_NAMES[channel] || channel)} ${delta > 0 ? '+' : ''}${number(delta)}`).join(' · ')}</p>` : ''}</div></article>`).join('')}</div></section>
    </div></div>`}
    ${account.role === 'supervisor' ? registerDialog() : ''}
    ${account.role === 'supervisor' && calibration ? `<dialog id="calibration-amend"><div class="dialog-heading"><h2>更正修正量</h2><button data-dismiss="calibration-amend" aria-label="关闭">×</button></div><p>更正后以新结果为准，不再成立的待办会自动撤销。</p><form class="fields" data-form="calibration-amend" data-calibration="${calibration.id}"><div class="fields full">${correctionFields(calibration.corrections)}</div><label class="full">更正说明<input name="note" required minlength="2" maxlength="300" value="${safe(calibration.note)}"></label><button class="primary full">保存并重新重算</button></form></dialog>` : ''}`;
}

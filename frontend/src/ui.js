import {CHANNELS, EVENT_LABELS, STATES, VERDICTS} from './labels.js';

const entities = {'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'};
export const safe = value => String(value ?? '').replace(/[&<>"']/g, char => entities[char]);
export const number = value => new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 3}).format(value);
export const time = value => value ? new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hourCycle:'h23'}).format(new Date(value)) : '—';
export const channelName = key => CHANNELS.find(([id]) => id === key)?.[1] || key;
export const lotLink = (page, id) => `/${page}?lot=${Number(id)}`;

export const status = value => `<span class="status ${safe(value)}">${safe(STATES[value] || value)}</span>`;
export const options = (values, current) => Object.entries(values).map(([value, label]) => `<option value="${safe(value)}" ${value === current ? 'selected' : ''}>${safe(label)}</option>`).join('');
export const empty = (heading, copy) => `<div class="empty"><strong>${safe(heading)}</strong><p>${safe(copy)}</p></div>`;
export const brand = () => '<span class="logomark" aria-hidden="true"><i></i><i></i><i></i></span><span class="brand-text">禾堤食品<small>HEDI FOODS</small></span>';
export const heading = (label, title, subtitle, action = '') => `<header class="page-title"><div><p class="eyebrow">${label}</p><h1>${title}</h1><p class="lede">${subtitle}</p></div>${action}</header>`;
export const sectionTitle = (title, sub = '') => `<div class="section-title"><h2>${title}</h2>${sub ? `<span>${sub}</span>` : ''}</div>`;

export function packLabel(lot, marks = [], zone = 'front') {
  return `<div class="sample-stage"><div class="sample-guides"><span>0</span><span>50</span><span>100</span></div>
    <div class="sample-bag ${safe(lot.machine.toLowerCase())}" aria-label="包装位置示意图">
      <div class="seal top"></div><div class="seal bottom"></div><div class="bag-paper">
        <span class="bag-brand">禾 堤</span><strong>${safe(lot.product.replace(/\s+\d+\s*g$/i,''))}</strong><span>PACKAGING / ${safe(lot.machine)}</span><div class="registration"><i></i><i></i><i></i><i></i></div>
      </div>${marks.filter(mark => mark.zone === zone).map(mark => `<span class="pin ${safe(mark.state)}" title="${safe(mark.note)}" style="--x:${mark.x_pct}%;--y:${mark.y_pct}%"></span>`).join('')}
    </div><span class="sample-caption">包装位置示意 · ${safe(lot.film_sku)}</span></div>`;
}

export function lotPicker(lots, current, page) {
  return `<label class="picker">当前批次<select data-lot-picker="${page}">${lots.map(lot => `<option value="${lot.id}" ${lot.id === current ? 'selected' : ''}>${safe(lot.code)} · ${safe(lot.product)}</option>`).join('')}</select></label>`;
}

export function channelInputs(values = {}, limits = {}) {
  return CHANNELS.map(([key, label, unit, max]) => `<label><span>${label} <small class="unit">${unit}${limits[key] ? ` · 上限 ${limits[key]}` : ''}</small></span><input name="${key}" aria-label="${label}" type="number" min="0" max="${max}" step="any" value="${safe(values[key] ?? '')}" placeholder="—"></label>`).join('');
}

export function readings(measure, limits) {
  return `<div class="channel-list">${CHANNELS.map(([key,label,unit]) => `<div class="channel-row"><span>${label}</span><b class="${measure[key] == null ? 'missing' : ''}">${measure[key] == null ? '缺失' : `${number(measure[key])} <small>${unit}</small>`}</b>${limits ? `<small>上限 ${number(limits[key])}</small>` : ''}</div>`).join('')}</div>`;
}

export function auditList(events) {
  return `<div class="timeline">${events.map(event => `<article><i></i><div><strong>${safe(EVENT_LABELS[event.action] || event.action)}</strong><small>${safe(event.actor)} · ${time(event.at)}</small>${auditNote(event)}</div></article>`).join('')}</div>`;
}

function auditNote(event) {
  const {sample, revision, missing, exceeds, reason, note, model, final, quantity, sku,
    original_model_grade, recomputed_grade, verdict, kind} = event.details;
  const taskLabel = event.action?.startsWith('calibration_task_') && kind
    ? `待办 ${kind === 'close_hold' ? '关批拦截' : '已关批追查'}` : '';
  return [sample && `${sample}${revision ? ` · v${revision}` : ''}`, model && `模型 ${model} → 人工 ${final}`,
    original_model_grade && `原模型 ${original_model_grade} → 重算 ${recomputed_grade || '—'}${verdict ? `（${VERDICTS[verdict] || verdict}）` : ''}`,
    taskLabel,
    reason || note, quantity && `${sku} · ${number(quantity)} 件`, missing?.length && `缺失 ${missing.map(channelName).join('、')}`,
    exceeds?.length && `超限 ${exceeds.map(channelName).join('、')}`].filter(Boolean).map(line => `<p>${safe(line)}</p>`).join('');
}

import {auditList, channelInputs, empty, heading, lotLink, lotPicker, number, readings, safe, sectionTitle, status, time} from '../ui.js';

export function reworkPage(lots, data, account) {
  const pending = lots.filter(lot => lot.rework_state);
  const r = data?.rework;
  const lot = data?.lot;
  return `${heading('包装线 / 返工与放行', '返工领料','按签核范围领取膜材，复测换膜区段后交接放行。',pending.length && r ? lotPicker(pending,lot.id,'rework') : '')}
    ${!r ? `${empty('所选批次没有返工需求','主管签核改判或非 A 级时，系统会生成领料单。')}${pending.length ? '<a data-nav href="/rework">查看已有返工单 →</a>' : ''}` : `
    <div class="lot-strip"><div><small>返工单</small><b>RW-${String(r.id).padStart(5,'0')}</b></div><div><small>包装批次</small><b>${safe(lot.code)}</b></div><div><small>产品 / 机台</small><b>${safe(lot.product)} · ${safe(lot.machine)}</b></div>${status(r.state)}<a data-nav href="${lotLink('workbench',lot.id)}">返回判级 →</a></div>
    <div class="rework-grid"><section class="panel ticket">${sectionTitle('备料交接单', r.trigger === 'override' ? '人工改判' : '非 A 级处置')}
      <p class="ticket-cause">${safe(r.reason)}</p><div class="ticket-big"><div class="warehouse-tile"><small>仓储库位</small><strong>${safe(r.bin)}</strong></div><div><small>换膜范围</small><strong>${number(r.required_units)} <span>件</span></strong></div></div>
      <dl class="ticket-meta"><dt>物料</dt><dd>${safe(r.description)}</dd><dt>膜材 SKU</dt><dd class="mono">${safe(r.sku)}</dd><dt>当前库位库存</dt><dd>${number(r.on_hand)} 件</dd><dt>领料操作人</dt><dd>${safe(r.issued_by || '待确认')} ${r.issued_at ? time(r.issued_at) : ''}</dd></dl>
      ${r.state === 'requested' && account.role === 'operator' ? `<button class="primary" data-action="rework/issue" ${r.on_hand < r.required_units ? 'disabled' : ''}>确认领料 · 扣减 ${number(r.required_units)} 件</button>${r.on_hand < r.required_units ? '<p class="notice warn">膜材余量不足，等待仓储补货。</p>' : ''}` : ''}
      ${r.state === 'requested' && account.role !== 'operator' ? '<p class="notice">待线边操作员确认膜材料位并完成出库。</p>' : ''}
      ${r.state !== 'requested' ? `<p class="notice">已领出 ${number(r.required_units)} 件膜材，出库已过账。</p>` : ''}
    </section><div class="stack"><section class="panel verify-panel">${sectionTitle('换膜复测', '放行条件')}
      <ol class="process"><li class="done"><span></span>生成需求</li><li class="${r.state !== 'requested' ? 'done' : 'active'}"><span></span>领料过账</li><li class="${r.state === 'verified' ? 'done' : r.state === 'issued' ? 'active' : ''}"><span></span>复测签核</li><li class="${lot.state === 'closed' ? 'done' : lot.state === 'approved' ? 'active' : ''}"><span></span>批次关闭</li></ol>
      ${r.state === 'requested' ? '<p class="muted">完成领料后，由主管登记同机台的五通道复测结果及实际换膜件数。</p>' : ''}
      ${r.state === 'issued' && account.role === 'supervisor' ? `<form data-action="rework/verify" class="fields"><input type="hidden" name="machine" value="${safe(lot.machine)}"><label>复测样本号<input name="sample" maxlength="64" required placeholder="${safe(lot.code)}-R01"></label><label>实际换膜件数<input name="replaced_units" type="number" min="1" max="${r.required_units}" value="${r.required_units}" required></label>${channelInputs({},data.verification_limits)}<label class="full">复测说明<input required minlength="2" maxlength="300" name="note"></label><button class="primary full">签核换膜复测</button></form>` : ''}
      ${r.state === 'issued' && account.role !== 'supervisor' ? '<p class="notice">等待质检主管签核换膜复测。</p>' : ''}
      ${r.state === 'verified' ? `<p class="notice">${safe(r.verification_note)} · ${safe(r.verified_by)} ${time(r.verified_at)}</p>${readings(data.measures.find(item=>item.id===r.verification_id),data.verification_limits)}` : ''}
      ${lot.state === 'approved' && account.role === 'supervisor' ? '<button class="primary" data-action="close">关闭批次</button>' : ''}
    </section><section class="panel trace">${sectionTitle('返工履历')}${auditList(data.events.filter(item=>['grade_signed','rework_requested','material_issued','rework_retest_rejected','rework_verified','lot_closed'].includes(item.action)))}</section></div></div>`}`;
}

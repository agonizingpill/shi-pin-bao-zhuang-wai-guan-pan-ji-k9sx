import {DEFECTS, GRADES, ZONES} from '../labels.js';
import {auditList, channelInputs, channelName, empty, heading, lotLink, lotPicker, number, options, packLabel, readings, safe, sectionTitle, status, time} from '../ui.js';

function gradePanel(data, decision, account) {
  const {lot} = data;
  if (!decision?.evidence) return `<section class="panel model">${sectionTitle('等级判别', 'PCA / LINEARSVC')}${decision?.state === 'queued' ? '<p class="notice">计算已入队。模型结果回写后，工作台会自动更新。</p>' : decision?.failure ? `<p class="notice warn">${safe(decision.failure)}</p>` : '<p class="muted">完整归档五个通道后，可提交模型判级。</p>'}</section>`;
  const {evidence} = decision;
  const scores = Object.entries(evidence.scores);
  const max = Math.max(...scores.map(([,score])=>Math.abs(score)), 0.01);
  return `<section class="panel model">${sectionTitle('等级判别', 'PCA / LINEARSVC')}
    <div class="featured-grade"><strong>${safe(decision.model_grade)}</strong><div><small>模型建议</small><h3>${GRADES[decision.model_grade]}</h3><p>${decision.final_grade ? `人工签核 ${safe(decision.final_grade)} 级` : '等待质检主管签核'}</p></div></div>
    <div class="scores">${scores.map(([grade, score])=> `<div><span>${grade}</span><div class="score-track"><i class="${score < 0 ? 'negative' : 'positive'}" style="--width:${Math.abs(score)/max*48}%"></i></div><b>${score.toFixed(2)}</b></div>`).join('')}</div>
    <div class="model-caption">线性间隔分数 · 正负值表示超平面方向</div>
    <details class="model-proof"><summary>查看模型凭证</summary><dl><dt>模型版本</dt><dd>${safe(evidence.version)}</dd><dt>输入校验</dt><dd class="digest">${safe(decision.input_sha256)}</dd><dt>参考样本校验</dt><dd class="digest">${safe(evidence.reference_sha256)}</dd><dt>合成留出准确率</dt><dd>${(evidence.holdout_accuracy*100).toFixed(1)}% · ${evidence.holdout_rows} 条</dd><dt>主成分坐标</dt><dd>${evidence.components.map(value=>value.toFixed(2)).join(' / ')}</dd><dt>方差贡献率</dt><dd>${evidence.variance_ratio.map(value=>(value*100).toFixed(1)+'%').join(' / ')}</dd><dt>固定种子</dt><dd>${evidence.seed}</dd></dl></details>
    ${lot.state === 'review' && account.role === 'supervisor' ? `<form class="fields sign-form" data-action="grade/review"><label>签核等级<select name="grade" data-grade data-model="${decision.model_grade}">${options(GRADES, decision.model_grade)}</select></label><div class="grade-rule">改判或非 A 级会生成返工领料。</div><div class="fields full" data-rework-fields ${decision.model_grade === 'A' ? 'hidden' : ''}><label>返工件数<input type="number" name="rework_units" min="1" max="${lot.units}" ${decision.model_grade === 'A' ? 'disabled' : 'required'}></label><label>判级理由<input name="reason" maxlength="300" ${decision.model_grade === 'A' ? 'disabled' : 'required'}></label></div><button class="primary full">签核等级</button></form>` : ''}
    ${decision.reason ? `<p class="notice">${safe(decision.reason)}</p>` : ''}
  </section>`;
}

export function workbenchPage(lots, data, account, zone) {
  if (!data) return `${heading('包装线 / 视觉质检', '判级工作台','从批次台账打开包装批次，开始视觉采样。')}${empty('找不到所选批次','请回到批次台账选择有效记录。')}<a data-nav href="/lots">返回包装批次 →</a>`;
  const {lot, current, measures, missing, marks, decisions, rework, events} = data;
  const decision = decisions.find(item => item.measure_id === current?.id);
  const currentMarks = marks.filter(mark => mark.measure_id === current?.id);
  const canCapture = account.role === 'operator' && ['new','blocked','measured'].includes(lot.state);
  const canMark = current && ['blocked','measured','review'].includes(lot.state);
  return `${heading('包装线 / 视觉质检','判级工作台','核对检测通道，定位包装缺陷并签核模型等级。',lotPicker(lots,lot.id,'workbench'))}
    <div class="lot-strip"><div><small>包装批次</small><b>${safe(lot.code)}</b></div><div><small>产品</small><b>${safe(lot.product)}</b></div><div><small>检测机台 / 件数</small><b>${safe(lot.machine)} / ${number(lot.units)}</b></div>${status(lot.state)}${rework ? `<a data-nav href="${lotLink('rework',lot.id)}">查看返工单 →</a>` : ''}${lot.state === 'approved' && account.role === 'supervisor' ? '<button class="primary" data-action="close">关闭批次</button>' : ''}</div>
    <div class="workbench-grid"><div class="stack"><section class="panel specimen">${sectionTitle('包装定位', current ? `测量 v${current.revision}` : '待采样')}${packLabel(lot,currentMarks,zone)}<div class="zones" aria-label="选择包装视图">${Object.entries(ZONES).map(([key,label])=> `<button type="button" data-zone="${key}" aria-pressed="${key === zone}">${label}</button>`).join('')}</div><p class="sample-ref mono">${safe(current?.sample || '未归档样本')}</p></section>
    <section class="panel">${sectionTitle('缺陷标记', `${currentMarks.length} 处`)}${currentMarks.length ? `<div class="marks-list">${currentMarks.map(mark=> `<article><div><b>${DEFECTS[mark.kind]}</b> <small>${ZONES[mark.zone]} · ${mark.x_pct}, ${mark.y_pct}</small>${status(mark.state)}</div><p>${safe(mark.note)}</p>${mark.review_note ? `<small>${safe(mark.reviewed_by)} · ${safe(mark.review_note)}</small>` : ''}${canMark && account.role === 'supervisor' && mark.state === 'open' ? `<form data-action="marks/${mark.id}/review" class="mark-review"><label>复核说明<input name="note" required minlength="2" maxlength="300"></label><button name="state" value="confirmed">确认</button><button name="state" value="dismissed">驳回</button></form>` : ''}</article>`).join('')}</div>` : empty('当前样本暂无标记','录入缺陷所在面、坐标和观察说明。')}
    ${canMark && account.role === 'operator' ? '<button data-dialog="mark">＋ 标记缺陷</button>' : ''}</section></div>
    <div class="stack"><section class="panel readings-panel">${sectionTitle('视觉测量', '相机读数')}${current ? readings(current) : empty('尚无视觉测量','用绑定机台的检测文件录入五项通道。')}
      ${current && missing.length ? `<p class="notice warn">缺失 ${missing.map(channelName).join('、')}。补采后保留原版本。</p>` : ''}
      <div class="button-line">${canCapture ? `<button data-dialog="capture">${current ? '归档补采版本' : '归档视觉测量'}</button>` : ''}${account.role === 'operator' && lot.state === 'measured' ? '<button class="primary" data-action="grade">提交判级</button>' : ''}</div>
      ${measures.length ? `<details class="model-proof"><summary>样本版本 · ${measures.length}</summary><ul class="samples">${measures.map(measure=> `<li>v${measure.revision} · ${safe(measure.sample)} <small>${measure.purpose === 'grading' ? '判级' : '返工复测'} · ${time(measure.created_at)}</small>${measure.id !== current?.id ? `<ul>${marks.filter(mark=>mark.measure_id===measure.id).map(mark=>`<li>${DEFECTS[mark.kind]} · ${ZONES[mark.zone]} ${mark.x_pct}, ${mark.y_pct} · ${safe(mark.note)} ${status(mark.state)}</li>`).join('')}</ul>` : ''}</li>`).join('')}</ul></details>` : ''}
    </section>${gradePanel(data,decision,account)}</div>
    <aside class="panel trace">${sectionTitle('批次履历', '按时间倒序')}${auditList(events)}</aside></div>
    ${canCapture ? `<dialog id="capture"><div class="dialog-heading"><h2>归档视觉测量</h2><button data-dismiss="capture" aria-label="关闭">×</button></div><p>机台 ${safe(lot.machine)}。通道未读取时留空，批次进入补采。</p><form class="fields" data-action="measurements"><input type="hidden" name="machine" value="${safe(lot.machine)}"><label class="full">新样本号<input name="sample" maxlength="64" required placeholder="${safe(lot.code)}-S0${(current?.revision || 0)+1}"></label>${channelInputs(current || {})}<button class="primary full">保存测量版本</button></form></dialog>` : ''}
    ${canMark && account.role === 'operator' ? `<dialog id="mark"><div class="dialog-heading"><h2>标记缺陷位置</h2><button data-dismiss="mark" aria-label="关闭">×</button></div><form class="fields" data-action="marks"><label>缺陷类型<select name="kind">${options(DEFECTS)}</select></label><label>包装面<select name="zone">${options(ZONES,zone)}</select></label><label>横向位置 %<input type="number" min="0" max="100" name="x_pct" required></label><label>纵向位置 %<input type="number" min="0" max="100" name="y_pct" required></label><label class="full">现场观察<textarea name="note" required minlength="2" maxlength="300"></textarea></label><button class="primary full">保存缺陷标记</button></form></dialog>` : ''}`;
}

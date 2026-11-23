"""Business queries over authoritative project records."""

VIEWS = {
    'rework-supply': (
        '返工物料追溯',
        '沿返工单核对领用 SKU、库位和复测证据，用于班末交接和缺证补录。',
        ['批次', '返工单', 'SKU', '库位', '触发原因', '状态', '复测测量号', '下一步'],
        """
SELECT l.code,r.id,r.sku,COALESCE(s.bin,'未配置'),r.reason,r.state,r.verification_id,
       CASE WHEN s.sku IS NULL THEN '核对库位' WHEN r.issued_at IS NULL THEN '等待领料'
            WHEN r.verification_id IS NULL THEN '等待补录复测' ELSE '复测已关联' END
FROM rework_issue r JOIN pack_lot l ON l.id=r.lot_id LEFT JOIN material_stock s ON s.sku=r.sku ORDER BY r.id DESC
""",
    ),
    'calibration-disposition': (
        '校准处置追踪',
        '核对机台校准勘误的原判、重算等级与关批拦截/已关批追查进度。',
        ['批次', '机台', '原模型', '人工签核', '重算等级', '对照', '勘误', '待办', '批次工序'],
        """
SELECT l.code,c.machine,s.prior_model_grade,s.prior_final_grade,r.recomputed_grade,
       CASE r.verdict WHEN 'lower' THEN '低于原判' WHEN 'same' THEN '与原判一致'
            WHEN 'higher' THEN '高于原判' ELSE COALESCE(r.failure,'尚未重算') END AS comparison,
       'CL-' || lpad(c.id::text,4,'0') || ' v' || c.revision AS calibration_ref,
       COALESCE(t.kind || '/' || t.state, '无') AS disposition,
       l.state
FROM calibration_result r
JOIN calibration c ON c.id=r.calibration_id
JOIN calibration_scope s ON s.calibration_id=r.calibration_id AND s.lot_id=r.lot_id
JOIN pack_lot l ON l.id=r.lot_id
LEFT JOIN LATERAL (SELECT kind,state FROM calibration_task
                   WHERE calibration_id=r.calibration_id AND lot_id=r.lot_id
                   ORDER BY (state='pending') DESC, id DESC LIMIT 1) t ON true
ORDER BY r.calibration_id DESC, r.lot_id
""",
    ),
}

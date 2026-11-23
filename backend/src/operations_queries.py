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
}

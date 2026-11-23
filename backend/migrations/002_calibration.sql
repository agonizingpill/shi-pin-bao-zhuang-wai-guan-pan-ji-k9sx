-- 机台校准勘误：按受影响时段重算已签批次的模型等级，并派生关批拦截/追查待办。
CREATE TABLE IF NOT EXISTS calibration (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    machine text NOT NULL CHECK (machine IN ('VS-01', 'VS-02', 'VS-03')),
    window_start timestamptz NOT NULL,
    window_end timestamptz NOT NULL,
    corrections jsonb NOT NULL,
    note text NOT NULL,
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    scope_version integer NOT NULL DEFAULT 0,
    state text NOT NULL DEFAULT 'accepted' CHECK (state IN ('accepted', 'computed')),
    failure text,
    created_by text NOT NULL REFERENCES staff(username),
    created_at timestamptz NOT NULL DEFAULT now(),
    amended_by text REFERENCES staff(username),
    amended_at timestamptz,
    computed_at timestamptz,
    CHECK (window_end > window_start)
);
CREATE INDEX IF NOT EXISTS calibration_pending ON calibration(id) WHERE state = 'accepted';

-- 受理勘误时冻结的已签样本范围：保留原读数、原模型等级与人工签核快照。
CREATE TABLE IF NOT EXISTS calibration_scope (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    calibration_id bigint NOT NULL REFERENCES calibration(id) ON DELETE CASCADE,
    lot_id bigint NOT NULL REFERENCES pack_lot(id),
    measure_id bigint NOT NULL REFERENCES visual_measure(id),
    decision_id bigint NOT NULL REFERENCES grade_decision(id),
    sample text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    sampled_at timestamptz NOT NULL,
    prior_model_grade char(1) NOT NULL CHECK (prior_model_grade IN ('A', 'B', 'C')),
    prior_final_grade char(1) NOT NULL CHECK (prior_final_grade IN ('A', 'B', 'C')),
    prior_reviewed_by text NOT NULL REFERENCES staff(username),
    prior_reviewed_at timestamptz NOT NULL,
    readings jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (calibration_id, lot_id)
);
CREATE INDEX IF NOT EXISTS calibration_scope_lot ON calibration_scope(lot_id, calibration_id);

-- 同一勘误同一批次只保留最新一次重算结果，修正量更新后覆盖并以新结果为准。
CREATE TABLE IF NOT EXISTS calibration_result (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    calibration_id bigint NOT NULL REFERENCES calibration(id) ON DELETE CASCADE,
    scope_id bigint NOT NULL REFERENCES calibration_scope(id) ON DELETE CASCADE,
    lot_id bigint NOT NULL REFERENCES pack_lot(id),
    measure_id bigint NOT NULL REFERENCES visual_measure(id),
    decision_id bigint NOT NULL REFERENCES grade_decision(id),
    revision integer NOT NULL CHECK (revision > 0),
    original_model_grade char(1) NOT NULL CHECK (original_model_grade IN ('A', 'B', 'C')),
    original_final_grade char(1) NOT NULL CHECK (original_final_grade IN ('A', 'B', 'C')),
    original_readings jsonb NOT NULL,
    corrected_readings jsonb,
    recomputed_grade char(1) CHECK (recomputed_grade IN ('A', 'B', 'C')),
    verdict text CHECK (verdict IN ('lower', 'same', 'higher')),
    state text NOT NULL DEFAULT 'computed' CHECK (state IN ('computed', 'error')),
    failure text,
    input_sha256 text,
    evidence jsonb,
    computed_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (calibration_id, lot_id)
);
CREATE INDEX IF NOT EXISTS calibration_result_lot ON calibration_result(lot_id);
CREATE INDEX IF NOT EXISTS calibration_result_lower ON calibration_result(calibration_id) WHERE verdict = 'lower';

-- 关批拦截与已关批追查待办。同一勘误、批次、类型最多一行，重试不会重复挂起。
CREATE TABLE IF NOT EXISTS calibration_task (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    calibration_id bigint NOT NULL REFERENCES calibration(id) ON DELETE CASCADE,
    lot_id bigint NOT NULL REFERENCES pack_lot(id),
    kind text NOT NULL CHECK (kind IN ('close_hold', 'investigation')),
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'cleared', 'cancelled')),
    revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
    detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    cleared_by text REFERENCES staff(username),
    cleared_at timestamptz,
    clear_note text,
    UNIQUE (calibration_id, lot_id, kind)
);
CREATE INDEX IF NOT EXISTS calibration_task_pending_lot ON calibration_task(lot_id, kind) WHERE state = 'pending';

CREATE TABLE IF NOT EXISTS calibration_event (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    calibration_id bigint NOT NULL REFERENCES calibration(id) ON DELETE CASCADE,
    actor text NOT NULL REFERENCES staff(username),
    action text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS calibration_event_calibration ON calibration_event(calibration_id, id DESC);

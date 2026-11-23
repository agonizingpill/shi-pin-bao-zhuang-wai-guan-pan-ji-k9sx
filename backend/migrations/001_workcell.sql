CREATE TABLE IF NOT EXISTS staff (
    username text PRIMARY KEY,
    display_name text NOT NULL,
    role text NOT NULL CHECK (role IN ('operator', 'supervisor', 'viewer')),
    password_hash text NOT NULL
);
CREATE TABLE IF NOT EXISTS material_stock (
    sku text PRIMARY KEY,
    description text NOT NULL,
    bin text NOT NULL,
    on_hand integer NOT NULL CHECK (on_hand >= 0)
);
CREATE TABLE IF NOT EXISTS pack_lot (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code text NOT NULL UNIQUE,
    product text NOT NULL,
    machine text NOT NULL CHECK (machine IN ('VS-01', 'VS-02', 'VS-03')),
    units integer NOT NULL CHECK (units > 0),
    film_sku text NOT NULL REFERENCES material_stock(sku),
    state text NOT NULL DEFAULT 'new' CHECK (state IN
        ('new', 'blocked', 'measured', 'queued', 'review', 'rework', 'approved', 'closed')),
    created_by text NOT NULL REFERENCES staff(username),
    created_at timestamptz NOT NULL DEFAULT now(),
    closed_by text REFERENCES staff(username),
    closed_at timestamptz
);
CREATE TABLE IF NOT EXISTS visual_measure (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lot_id bigint NOT NULL REFERENCES pack_lot(id),
    sample text NOT NULL UNIQUE,
    revision integer NOT NULL CHECK (revision > 0),
    machine text NOT NULL CHECK (machine IN ('VS-01', 'VS-02', 'VS-03')),
    purpose text NOT NULL CHECK (purpose IN ('grading', 'verification')),
    replaces_id bigint REFERENCES visual_measure(id),
    wrinkle_pct double precision CHECK (wrinkle_pct BETWEEN 0 AND 100),
    offset_mm double precision CHECK (offset_mm BETWEEN 0 AND 20),
    delta_e double precision CHECK (delta_e BETWEEN 0 AND 60),
    seal_gap_mm double precision CHECK (seal_gap_mm BETWEEN 0 AND 12),
    gloss_cv double precision CHECK (gloss_cv BETWEEN 0 AND 100),
    created_by text NOT NULL REFERENCES staff(username),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (lot_id, revision)
);
CREATE TABLE IF NOT EXISTS defect_mark (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    measure_id bigint NOT NULL REFERENCES visual_measure(id),
    kind text NOT NULL CHECK (kind IN ('wrinkle', 'offset', 'color', 'seal', 'gloss')),
    zone text NOT NULL CHECK (zone IN ('front', 'back', 'top-seal', 'bottom-seal')),
    x_pct integer NOT NULL CHECK (x_pct BETWEEN 0 AND 100),
    y_pct integer NOT NULL CHECK (y_pct BETWEEN 0 AND 100),
    note text NOT NULL,
    state text NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'confirmed', 'dismissed')),
    created_by text NOT NULL REFERENCES staff(username),
    created_at timestamptz NOT NULL DEFAULT now(),
    reviewed_by text REFERENCES staff(username),
    reviewed_at timestamptz,
    review_note text,
    UNIQUE (measure_id, kind, zone, x_pct, y_pct)
);
CREATE TABLE IF NOT EXISTS grade_decision (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    measure_id bigint NOT NULL REFERENCES visual_measure(id),
    state text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'predicted', 'failed', 'signed')),
    input_sha256 text NOT NULL,
    model_grade char(1) CHECK (model_grade IN ('A', 'B', 'C')),
    final_grade char(1) CHECK (final_grade IN ('A', 'B', 'C')),
    evidence jsonb,
    failure text,
    reason text,
    requested_by text NOT NULL REFERENCES staff(username),
    requested_at timestamptz NOT NULL DEFAULT now(),
    computed_at timestamptz,
    reviewed_by text REFERENCES staff(username),
    reviewed_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_decision ON grade_decision(measure_id) WHERE state <> 'failed';
CREATE INDEX IF NOT EXISTS queued_decisions ON grade_decision(id) WHERE state = 'queued';
CREATE TABLE IF NOT EXISTS rework_issue (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lot_id bigint NOT NULL UNIQUE REFERENCES pack_lot(id),
    decision_id bigint NOT NULL UNIQUE REFERENCES grade_decision(id),
    sku text NOT NULL REFERENCES material_stock(sku),
    required_units integer NOT NULL CHECK (required_units > 0),
    reason text NOT NULL,
    trigger text NOT NULL CHECK (trigger IN ('override', 'out_of_grade')),
    state text NOT NULL DEFAULT 'requested' CHECK (state IN ('requested', 'issued', 'verified')),
    created_at timestamptz NOT NULL DEFAULT now(),
    issued_by text REFERENCES staff(username),
    issued_at timestamptz,
    verification_id bigint UNIQUE REFERENCES visual_measure(id),
    verified_by text REFERENCES staff(username),
    verified_at timestamptz,
    verification_note text
);
CREATE TABLE IF NOT EXISTS audit_event (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lot_id bigint NOT NULL REFERENCES pack_lot(id),
    actor text NOT NULL REFERENCES staff(username),
    action text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_lot ON audit_event(lot_id, id DESC);

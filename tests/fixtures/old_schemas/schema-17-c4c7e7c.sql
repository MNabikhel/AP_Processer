-- A new database as AP Coder made it at c4c7e7c (PR #47); the same at b3002bb (PR #48) (scripts/old_schemas.py)
-- schema 17
CREATE TABLE gl_accounts (
    code TEXT PRIMARY KEY, description TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, meals INTEGER NOT NULL DEFAULT 0);
CREATE TABLE cost_centers (
    code TEXT PRIMARY KEY, description TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL);
CREATE TABLE tax_treatments (
    tax_type TEXT PRIMARY KEY, treatment TEXT NOT NULL, gl_code TEXT NOT NULL DEFAULT '');
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path TEXT NOT NULL, file_name TEXT NOT NULL, file_sha256 TEXT,
    status TEXT NOT NULL,
    vendor_name TEXT, vendor_key TEXT, invoice_number TEXT, invoice_date TEXT, currency TEXT, grand_total REAL,
    model_confidence REAL, adjusted_confidence REAL, requires_review INTEGER,
    ai_output TEXT, final_output TEXT, validation TEXT, extraction_md TEXT, meta TEXT, edits TEXT, error TEXT,
    created_at TEXT NOT NULL, reviewed_at TEXT, reviewer TEXT, export_batch INTEGER, po_key TEXT NOT NULL DEFAULT '', due_date TEXT, second_reviewer TEXT, second_reviewed_at TEXT, parked_reason TEXT, follow_up TEXT, original_sha256 TEXT);
CREATE TABLE feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER, line_number INTEGER,
    vendor_key TEXT NOT NULL, vendor_name TEXT NOT NULL, description TEXT NOT NULL, amount REAL,
    suggested_gl TEXT, final_gl TEXT NOT NULL, suggested_cc TEXT, final_cc TEXT,
    outcome TEXT NOT NULL, reviewer TEXT, created_at TEXT NOT NULL, final_tax_code TEXT NOT NULL DEFAULT '',
    tax_source TEXT NOT NULL DEFAULT '', tax_province TEXT NOT NULL DEFAULT '',
    tax_uncollected TEXT NOT NULL DEFAULT '');
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER, action TEXT NOT NULL, actor TEXT, detail TEXT, created_at TEXT NOT NULL);
CREATE TABLE vendors (
    vendor_key TEXT PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
    expected_gst TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL, erp_id TEXT NOT NULL DEFAULT '', terms TEXT NOT NULL DEFAULT '', default_gl TEXT NOT NULL DEFAULT '', in_master INTEGER NOT NULL DEFAULT 0, qst_number TEXT NOT NULL DEFAULT '', pst_number TEXT NOT NULL DEFAULT '', province TEXT NOT NULL DEFAULT '');
CREATE TABLE export_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, actor TEXT, format TEXT NOT NULL,
    invoices INTEGER NOT NULL, total REAL, undone_at TEXT, undone_by TEXT);
CREATE TABLE purchase_orders (
    po_key TEXT PRIMARY KEY, po_number TEXT NOT NULL, vendor_name TEXT NOT NULL DEFAULT '',
    vendor_key TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open', updated_at TEXT NOT NULL);
CREATE TABLE po_lines (
    po_key TEXT NOT NULL, line_number INTEGER NOT NULL, description TEXT NOT NULL, quantity REAL NOT NULL,
    unit_price REAL NOT NULL, amount REAL NOT NULL, received_quantity REAL, gl_code TEXT NOT NULL DEFAULT '',
    cost_center TEXT NOT NULL DEFAULT '', amount_only INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (po_key, line_number));
CREATE TABLE erp_invoices (
    vendor_key TEXT NOT NULL, number_key TEXT NOT NULL, total REAL NOT NULL, vendor_name TEXT NOT NULL,
    invoice_number TEXT NOT NULL, invoice_date TEXT NOT NULL DEFAULT '', imported_at TEXT NOT NULL,
    copies INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (vendor_key, number_key, total));
CREATE TABLE coding_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT, vendor TEXT NOT NULL DEFAULT '', contains TEXT NOT NULL DEFAULT '',
    gl_code TEXT NOT NULL, cost_center TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, created_by TEXT);
CREATE TABLE invoice_capture (
    invoice_id INTEGER PRIMARY KEY REFERENCES invoices (id) ON DELETE CASCADE, capture_json TEXT NOT NULL,
    layout_source TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE supplier_profiles (
    key TEXT PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '', vendor_id TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'supervised', autonomous_since TEXT, audit_rate REAL, template_json TEXT,
    updated_at TEXT NOT NULL, updated_by TEXT, suspended_at TEXT);
CREATE TABLE supplier_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, supplier_key TEXT NOT NULL, invoice_id INTEGER, field TEXT NOT NULL,
    ai_value TEXT, final_value TEXT, correct INTEGER NOT NULL, source TEXT NOT NULL DEFAULT 'review',
    at TEXT NOT NULL);
CREATE TABLE reader_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER, reader TEXT NOT NULL, field TEXT NOT NULL,
    read_value TEXT, final_value TEXT, correct INTEGER NOT NULL, evidence TEXT NOT NULL DEFAULT '',
    layout_source TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '', at TEXT NOT NULL);
CREATE TABLE page_reads (
    invoice_id INTEGER PRIMARY KEY, status TEXT NOT NULL, model TEXT NOT NULL DEFAULT '',
    pages INTEGER NOT NULL DEFAULT 0, seconds REAL NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '', requested_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, reader TEXT NOT NULL DEFAULT '', tries INTEGER NOT NULL DEFAULT 0);
CREATE TABLE azure_checks (
    invoice_id INTEGER PRIMARY KEY, reason TEXT NOT NULL, status TEXT NOT NULL, pages INTEGER NOT NULL DEFAULT 0,
    compared INTEGER NOT NULL DEFAULT 0, agreed INTEGER NOT NULL DEFAULT 0, result_json TEXT,
    error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, compared_at TEXT);
CREATE TABLE verifications (
    invoice_id INTEGER PRIMARY KEY, reason TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
    rounds INTEGER NOT NULL DEFAULT 0, seconds REAL NOT NULL DEFAULT 0, result_json TEXT,
    error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    tries INTEGER NOT NULL DEFAULT 0, reader TEXT NOT NULL DEFAULT '');
CREATE TABLE lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT, situation TEXT NOT NULL UNIQUE, vendor_key TEXT NOT NULL,
    vendor_name TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'invoice', field TEXT NOT NULL,
    pattern TEXT NOT NULL DEFAULT '', value TEXT NOT NULL DEFAULT '', proposed TEXT NOT NULL DEFAULT '',
    seen INTEGER NOT NULL DEFAULT 0, corrected INTEGER NOT NULL DEFAULT 0, since_why INTEGER NOT NULL DEFAULT 0,
    confirmed INTEGER NOT NULL DEFAULT 0, explanation TEXT NOT NULL DEFAULT '', explained_by TEXT NOT NULL DEFAULT '',
    explained_at TEXT, retired INTEGER NOT NULL DEFAULT 0, last_invoice INTEGER, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL);
CREATE TABLE lesson_approvals (
    invoice_id INTEGER NOT NULL, situation TEXT NOT NULL, corrected INTEGER NOT NULL DEFAULT 0,
    confirmed INTEGER NOT NULL DEFAULT 0, value TEXT NOT NULL DEFAULT '', proposed TEXT NOT NULL DEFAULT '',
    at TEXT NOT NULL, PRIMARY KEY (invoice_id, situation));
CREATE TABLE bank_accounts (
    code TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', currency TEXT NOT NULL DEFAULT 'CAD',
    institution TEXT NOT NULL DEFAULT '', transit TEXT NOT NULL DEFAULT '', account_number TEXT NOT NULL DEFAULT '',
    gl_account TEXT NOT NULL DEFAULT '', is_default INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL);
CREATE TABLE invoice_drafts (
    invoice_id INTEGER NOT NULL, person TEXT NOT NULL DEFAULT '', draft TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (invoice_id, person));
CREATE TABLE template_snapshots (seq INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER NOT NULL UNIQUE, supplier_key TEXT NOT NULL, before_json TEXT, after_json TEXT, at TEXT NOT NULL);
INSERT INTO settings (key, value) VALUES ('vendor_key_rules', '2');
INSERT INTO settings (key, value) VALUES ('feedback_cleanup', '1');
INSERT INTO settings (key, value) VALUES ('reading_d89', '1');
INSERT INTO settings (key, value) VALUES ('schema_version', '17');
CREATE INDEX feedback_vendor ON feedback (vendor_key);
CREATE INDEX invoices_status ON invoices (status);
CREATE INDEX events_invoice ON events (invoice_id);
CREATE INDEX invoices_vendor ON invoices (vendor_key);
CREATE INDEX purchase_orders_vendor ON purchase_orders (vendor_key);
CREATE INDEX supplier_outcomes_key_at ON supplier_outcomes (supplier_key, at);
CREATE INDEX supplier_outcomes_invoice ON supplier_outcomes (invoice_id);
CREATE INDEX reader_outcomes_invoice ON reader_outcomes (invoice_id);
CREATE INDEX reader_outcomes_reader ON reader_outcomes (reader, field);
CREATE INDEX page_reads_status ON page_reads (status, created_at);
CREATE INDEX azure_checks_status ON azure_checks (status, created_at);
CREATE INDEX verifications_status ON verifications (status, created_at);
CREATE INDEX lessons_vendor ON lessons (vendor_key, kind);
CREATE INDEX lesson_approvals_situation ON lesson_approvals (situation);
CREATE INDEX invoices_po ON invoices (po_key);
CREATE INDEX invoices_original_sha ON invoices (original_sha256);
CREATE INDEX invoices_sha ON invoices (file_sha256);
CREATE INDEX invoices_total ON invoices (grand_total);
CREATE INDEX events_action ON events (action, invoice_id);
CREATE INDEX template_snapshots_supplier ON template_snapshots (supplier_key, seq);

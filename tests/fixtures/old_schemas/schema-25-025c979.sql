-- A new database as AP Coder made it at 025c979 (schema 25) (scripts/old_schemas.py)
-- schema 25
CREATE TABLE gl_accounts (
    code TEXT PRIMARY KEY, description TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, meals INTEGER NOT NULL DEFAULT 0);
CREATE TABLE cost_centers (
    code TEXT PRIMARY KEY, description TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, manager_name TEXT NOT NULL DEFAULT '', manager_email TEXT NOT NULL DEFAULT '');
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
    expected_gst TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL, erp_id TEXT NOT NULL DEFAULT '', terms TEXT NOT NULL DEFAULT '', default_gl TEXT NOT NULL DEFAULT '', in_master INTEGER NOT NULL DEFAULT 0, qst_number TEXT NOT NULL DEFAULT '', pst_number TEXT NOT NULL DEFAULT '', province TEXT NOT NULL DEFAULT '', erp_bank TEXT NOT NULL DEFAULT '', erp_hold TEXT NOT NULL DEFAULT '');
CREATE TABLE export_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, actor TEXT, format TEXT NOT NULL,
    invoices INTEGER NOT NULL, total REAL, undone_at TEXT, undone_by TEXT);
CREATE TABLE purchase_orders (
    po_key TEXT PRIMARY KEY, po_number TEXT NOT NULL, vendor_name TEXT NOT NULL DEFAULT '',
    vendor_key TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open', updated_at TEXT NOT NULL);
CREATE TABLE po_lines (
    po_key TEXT NOT NULL, line_number INTEGER NOT NULL, description TEXT NOT NULL, quantity REAL NOT NULL,
    unit_price REAL NOT NULL, amount REAL NOT NULL, received_quantity REAL, gl_code TEXT NOT NULL DEFAULT '',
    cost_center TEXT NOT NULL DEFAULT '', amount_only INTEGER NOT NULL DEFAULT 0, open_amount REAL, PRIMARY KEY (po_key, line_number));
CREATE TABLE erp_invoices (
    vendor_key TEXT NOT NULL, number_key TEXT NOT NULL, total REAL NOT NULL, vendor_name TEXT NOT NULL,
    invoice_number TEXT NOT NULL, invoice_date TEXT NOT NULL DEFAULT '', imported_at TEXT NOT NULL,
    copies INTEGER NOT NULL DEFAULT 1, plain_name TEXT NOT NULL DEFAULT '', raw_number TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (vendor_key, number_key, total));
CREATE TABLE coding_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT, vendor TEXT NOT NULL DEFAULT '', contains TEXT NOT NULL DEFAULT '',
    gl_code TEXT NOT NULL, cost_center TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, created_by TEXT);
CREATE TABLE invoice_capture (
    invoice_id INTEGER PRIMARY KEY REFERENCES invoices (id) ON DELETE CASCADE, capture_json TEXT NOT NULL,
    layout_source TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, name_status, credit_note);
CREATE TABLE supplier_profiles (
    key TEXT PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '', vendor_id TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'supervised', autonomous_since TEXT, audit_rate REAL, template_json TEXT,
    updated_at TEXT NOT NULL, updated_by TEXT, suspended_at TEXT);
CREATE TABLE supplier_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, supplier_key TEXT NOT NULL, invoice_id INTEGER, field TEXT NOT NULL,
    ai_value TEXT, final_value TEXT, correct INTEGER NOT NULL, source TEXT NOT NULL DEFAULT 'review',
    at TEXT NOT NULL);
CREATE TABLE vendor_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT, alias_key TEXT NOT NULL, printed TEXT NOT NULL DEFAULT '',
    vendor_name TEXT NOT NULL, gst_number TEXT NOT NULL DEFAULT '', invoice_id INTEGER, at TEXT NOT NULL);
CREATE TABLE line_texts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, supplier_key TEXT NOT NULL, read_key TEXT NOT NULL,
    printed TEXT NOT NULL DEFAULT '', description TEXT NOT NULL, corrected INTEGER NOT NULL DEFAULT 0,
    invoice_id INTEGER, at TEXT NOT NULL);
CREATE TABLE manager_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'waiting',
    sent_at TEXT NOT NULL, sent_by TEXT NOT NULL DEFAULT '', manager_name TEXT NOT NULL DEFAULT '',
    manager_email TEXT NOT NULL DEFAULT '', manager_user INTEGER, cost_centers TEXT NOT NULL DEFAULT '[]',
    lines TEXT NOT NULL DEFAULT '[]', note TEXT NOT NULL DEFAULT '', answered_at TEXT, answered_by TEXT NOT NULL
    DEFAULT '', answer_note TEXT NOT NULL DEFAULT '', reply_file TEXT NOT NULL DEFAULT '', via TEXT NOT NULL
    DEFAULT '');
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
CREATE TABLE bank_accounts (code TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '',
    currency TEXT NOT NULL DEFAULT 'CAD', institution TEXT NOT NULL DEFAULT '', transit TEXT NOT NULL DEFAULT '',
    account_number TEXT NOT NULL DEFAULT '', gl_account TEXT NOT NULL DEFAULT '', is_default INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL);
CREATE TABLE invoice_drafts (invoice_id INTEGER NOT NULL, person TEXT NOT NULL DEFAULT '',
    draft TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (invoice_id, person));
CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT '', role TEXT NOT NULL DEFAULT 'clerk', pw_hash TEXT NOT NULL, active INTEGER
    NOT NULL DEFAULT 1, must_change INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, last_login TEXT, cost_centers TEXT NOT NULL DEFAULT '', totp_secret TEXT NOT NULL DEFAULT '', totp_on INTEGER NOT NULL DEFAULT 0, totp_step INTEGER NOT NULL DEFAULT 0);
CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at REAL NOT NULL,
    last_seen REAL NOT NULL, ip TEXT NOT NULL DEFAULT '', user_agent TEXT NOT NULL DEFAULT '');
CREATE TABLE login_attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT '', at REAL NOT NULL, ok INTEGER NOT NULL DEFAULT 0, user_locked_until REAL,
    ip_locked_until REAL);
CREATE TABLE ephemeral (kind TEXT NOT NULL, token TEXT NOT NULL, value BLOB NOT NULL,
    created_at REAL NOT NULL, expires_at REAL NOT NULL, PRIMARY KEY (kind, token));
CREATE TABLE seen_files (path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, sha256 TEXT NOT NULL);
CREATE TABLE template_snapshots (seq INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER NOT NULL UNIQUE, supplier_key TEXT NOT NULL, before_json TEXT, after_json TEXT, at TEXT NOT NULL);
CREATE TABLE invoice_summary (id INTEGER PRIMARY KEY, status TEXT, file_name TEXT, vendor_name TEXT, vendor_key TEXT, invoice_number TEXT, invoice_date TEXT, due_date TEXT, currency TEXT, grand_total REAL, po_key TEXT, created_at TEXT, reviewed_at TEXT, reviewer TEXT, second_reviewer TEXT, second_reviewed_at TEXT, export_batch INTEGER, parked_reason TEXT, follow_up TEXT, error TEXT, requires_review INTEGER, model_confidence REAL, adjusted_confidence REAL, edits TEXT, demo INTEGER NOT NULL DEFAULT 0, gst_number, pay_from, po_number TEXT, subtotal REAL, tax_total REAL, gl_codes, first_gl, line_gls TEXT NOT NULL DEFAULT '[]', plain_name TEXT NOT NULL DEFAULT '', number_key TEXT NOT NULL DEFAULT '', raw_number TEXT NOT NULL DEFAULT '', tax_date TEXT NOT NULL DEFAULT '', f_date TEXT NOT NULL DEFAULT '', bill_vendor TEXT, bill_number TEXT, bill_credit INTEGER, po_billed REAL, p_vendor TEXT, p_currency TEXT, i_seconds REAL, a_at TEXT, a_corrected INTEGER, a_filled INTEGER, a_bulk INTEGER, pay_terms TEXT, pay_vendor TEXT, w_name TEXT, w_key TEXT, w_codes TEXT NOT NULL DEFAULT '[]', w_corrected INTEGER, q_audit INTEGER, q_stamp TEXT, q_found TEXT, r_stamp TEXT, r_found TEXT, q_pick TEXT, q_pick_cc TEXT, q_pay TEXT, legacy INTEGER NOT NULL DEFAULT 0, broken INTEGER NOT NULL DEFAULT 0);
CREATE TABLE summary_facts (id INTEGER PRIMARY KEY, i_cost TEXT, pay TEXT, t_facts TEXT, s_facts TEXT);
CREATE TABLE summary_codes (id INTEGER NOT NULL, code, n INTEGER NOT NULL, pos INTEGER NOT NULL, PRIMARY KEY (id, pos));
CREATE TABLE summary_spend (invoice_id INTEGER NOT NULL, part INTEGER NOT NULL, gl_code TEXT NOT NULL, cost_center TEXT NOT NULL, amount REAL NOT NULL, sizes REAL NOT NULL, cents REAL NOT NULL, amounts TEXT, PRIMARY KEY (invoice_id, part));
CREATE TABLE summary_dirty (id INTEGER PRIMARY KEY);
CREATE TABLE feedback_counts (outcome TEXT PRIMARY KEY, n INTEGER NOT NULL);
CREATE VIRTUAL TABLE invoice_search USING fts5(plain_name, raw_number, tokenize = 'trigram');
INSERT INTO settings (key, value) VALUES ('vendor_key_rules', '2');
INSERT INTO settings (key, value) VALUES ('feedback_cleanup', '1');
INSERT INTO settings (key, value) VALUES ('reading_d89', '1');
INSERT INTO settings (key, value) VALUES ('seen_files', '1');
INSERT INTO settings (key, value) VALUES ('schema_version', '25');
INSERT INTO settings (key, value) VALUES ('summary_rules', '2/2');
INSERT INTO settings (key, value) VALUES ('summary_id', '00000000000000000000000000000000');
CREATE INDEX feedback_vendor ON feedback (vendor_key);
CREATE INDEX invoices_status ON invoices (status);
CREATE INDEX events_invoice ON events (invoice_id);
CREATE INDEX invoices_vendor ON invoices (vendor_key);
CREATE INDEX purchase_orders_vendor ON purchase_orders (vendor_key);
CREATE INDEX supplier_outcomes_key_at ON supplier_outcomes (supplier_key, at);
CREATE INDEX supplier_outcomes_invoice ON supplier_outcomes (invoice_id);
CREATE INDEX vendor_aliases_key ON vendor_aliases (alias_key, id);
CREATE INDEX vendor_aliases_invoice ON vendor_aliases (invoice_id);
CREATE INDEX line_texts_key ON line_texts (supplier_key, id);
CREATE INDEX line_texts_invoice ON line_texts (invoice_id);
CREATE INDEX manager_requests_invoice ON manager_requests (invoice_id, id);
CREATE INDEX manager_requests_user ON manager_requests (manager_user, status);
CREATE INDEX reader_outcomes_invoice ON reader_outcomes (invoice_id);
CREATE INDEX reader_outcomes_reader ON reader_outcomes (reader, field);
CREATE INDEX page_reads_status ON page_reads (status, created_at);
CREATE INDEX azure_checks_status ON azure_checks (status, created_at);
CREATE INDEX verifications_status ON verifications (status, created_at);
CREATE INDEX lessons_vendor ON lessons (vendor_key, kind);
CREATE INDEX login_attempts_user ON login_attempts (username, at);
CREATE INDEX login_attempts_ip ON login_attempts (ip, at);
CREATE INDEX invoices_po ON invoices (po_key);
CREATE INDEX invoices_export_batch ON invoices (export_batch);
CREATE INDEX feedback_invoice ON feedback (invoice_id);
CREATE INDEX events_action_at ON events (action, created_at);
CREATE INDEX sessions_user ON sessions (user_id);
CREATE INDEX ephemeral_expires ON ephemeral (expires_at);
CREATE INDEX vendors_erp_id ON vendors (erp_id);
CREATE INDEX events_created ON events (created_at, action);
CREATE INDEX events_actor ON events (actor);
CREATE INDEX erp_invoices_number ON erp_invoices (number_key);
CREATE INDEX lesson_approvals_counted ON lesson_approvals (situation, corrected, value);
CREATE INDEX invoices_original_sha ON invoices (original_sha256);
CREATE INDEX invoices_sha ON invoices (file_sha256);
CREATE INDEX invoices_total ON invoices (grand_total);
CREATE INDEX events_action ON events (action, invoice_id);
CREATE INDEX template_snapshots_supplier ON template_snapshots (supplier_key, seq);
CREATE INDEX summary_codes_code ON summary_codes (code, n, id, pos);
CREATE INDEX summary_status_reviewed ON invoice_summary (status, reviewed_at);
CREATE INDEX summary_status_second ON invoice_summary (status, second_reviewed_at);
CREATE INDEX summary_vendor ON invoice_summary (vendor_key);
CREATE INDEX summary_ready ON invoice_summary (status, export_batch, reviewed_at);
CREATE INDEX summary_batch ON invoice_summary (export_batch);
CREATE INDEX summary_demo ON invoice_summary (demo);
CREATE INDEX summary_edits ON invoice_summary (status, edits);
CREATE INDEX summary_tax_date ON invoice_summary (tax_date);
CREATE INDEX summary_f_date ON invoice_summary (f_date);
CREATE INDEX summary_number ON invoice_summary (number_key);
CREATE INDEX summary_total ON invoice_summary (grand_total);
CREATE INDEX summary_po ON invoice_summary (po_key);
CREATE INDEX summary_bill ON invoice_summary (bill_number);
CREATE INDEX summary_legacy ON invoice_summary (legacy);
CREATE INDEX summary_broken ON invoice_summary (broken);
CREATE INDEX summary_vendors ON invoice_summary (status, vendor_key, vendor_name);
CREATE INDEX feedback_outcome ON feedback (outcome);
CREATE INDEX feedback_vendor_outcome ON feedback (vendor_key, outcome);
CREATE TRIGGER summary_invoice_added AFTER INSERT ON invoices BEGIN INSERT OR IGNORE INTO summary_dirty (id) VALUES (NEW.id); END;
CREATE TRIGGER summary_invoice_changed AFTER UPDATE ON invoices BEGIN INSERT OR IGNORE INTO summary_dirty (id) VALUES (OLD.id); INSERT OR IGNORE INTO summary_dirty (id) VALUES (NEW.id); END;
CREATE TRIGGER summary_invoice_deleted AFTER DELETE ON invoices BEGIN INSERT OR IGNORE INTO summary_dirty (id) VALUES (OLD.id); END;
CREATE TRIGGER summary_approval_added AFTER INSERT ON events WHEN NEW.action = 'approved' AND NEW.invoice_id IS NOT NULL BEGIN INSERT OR IGNORE INTO summary_dirty (id) VALUES (NEW.invoice_id); END;
CREATE TRIGGER summary_approval_changed AFTER UPDATE ON events WHEN 'approved' IN (OLD.action, NEW.action) BEGIN INSERT OR IGNORE INTO summary_dirty (id) VALUES (OLD.invoice_id); INSERT OR IGNORE INTO summary_dirty (id) VALUES (NEW.invoice_id); END;
CREATE TRIGGER summary_approval_deleted AFTER DELETE ON events WHEN OLD.action = 'approved' AND OLD.invoice_id IS NOT NULL BEGIN INSERT OR IGNORE INTO summary_dirty (id) VALUES (OLD.invoice_id); END;
CREATE TRIGGER summary_lesson_added AFTER INSERT ON feedback BEGIN INSERT INTO feedback_counts (outcome, n) VALUES (NEW.outcome, 1) ON CONFLICT(outcome) DO UPDATE SET n = n + 1; END;
CREATE TRIGGER summary_lesson_changed AFTER UPDATE OF outcome ON feedback BEGIN UPDATE feedback_counts SET n = n - 1 WHERE outcome = OLD.outcome; INSERT INTO feedback_counts (outcome, n) VALUES (NEW.outcome, 1) ON CONFLICT(outcome) DO UPDATE SET n = n + 1; END;
CREATE TRIGGER summary_lesson_deleted AFTER DELETE ON feedback BEGIN UPDATE feedback_counts SET n = n - 1 WHERE outcome = OLD.outcome; END;
CREATE TRIGGER summary_tax_added AFTER INSERT ON tax_treatments BEGIN INSERT OR IGNORE INTO summary_dirty (id) SELECT id FROM invoice_summary WHERE legacy = 1; END;
CREATE TRIGGER summary_tax_changed AFTER UPDATE ON tax_treatments BEGIN INSERT OR IGNORE INTO summary_dirty (id) SELECT id FROM invoice_summary WHERE legacy = 1; END;
CREATE TRIGGER summary_tax_deleted AFTER DELETE ON tax_treatments BEGIN INSERT OR IGNORE INTO summary_dirty (id) SELECT id FROM invoice_summary WHERE legacy = 1; END;
CREATE TRIGGER changes_vendors_added AFTER INSERT ON vendors BEGIN INSERT INTO settings (key, value) VALUES ('changes/vendors', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_vendors_changed AFTER UPDATE ON vendors BEGIN INSERT INTO settings (key, value) VALUES ('changes/vendors', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_vendors_deleted AFTER DELETE ON vendors BEGIN INSERT INTO settings (key, value) VALUES ('changes/vendors', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_approved_added AFTER INSERT ON invoices WHEN NEW.status = 'approved' BEGIN INSERT INTO settings (key, value) VALUES ('changes/approved', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_approved_changed AFTER UPDATE OF status, vendor_key, final_output, ai_output ON invoices WHEN (OLD.status IS 'approved') != (NEW.status IS 'approved') OR (NEW.status IS 'approved' AND (OLD.vendor_key IS NOT NEW.vendor_key OR OLD.final_output IS NOT NEW.final_output OR OLD.ai_output IS NOT NEW.ai_output)) BEGIN INSERT INTO settings (key, value) VALUES ('changes/approved', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_approved_deleted AFTER DELETE ON invoices WHEN OLD.status = 'approved' BEGIN INSERT INTO settings (key, value) VALUES ('changes/approved', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_supplier_outcomes_added AFTER INSERT ON supplier_outcomes BEGIN INSERT INTO settings (key, value) VALUES ('changes/suppliers', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_supplier_outcomes_changed AFTER UPDATE ON supplier_outcomes BEGIN INSERT INTO settings (key, value) VALUES ('changes/suppliers', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_supplier_outcomes_deleted AFTER DELETE ON supplier_outcomes BEGIN INSERT INTO settings (key, value) VALUES ('changes/suppliers', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_reader_outcomes_added AFTER INSERT ON reader_outcomes BEGIN INSERT INTO settings (key, value) VALUES ('changes/readers', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_reader_outcomes_changed AFTER UPDATE ON reader_outcomes BEGIN INSERT INTO settings (key, value) VALUES ('changes/readers', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_reader_outcomes_deleted AFTER DELETE ON reader_outcomes BEGIN INSERT INTO settings (key, value) VALUES ('changes/readers', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_purchase_orders_added AFTER INSERT ON purchase_orders BEGIN INSERT INTO settings (key, value) VALUES ('changes/purchase_orders', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_purchase_orders_changed AFTER UPDATE ON purchase_orders BEGIN INSERT INTO settings (key, value) VALUES ('changes/purchase_orders', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_purchase_orders_deleted AFTER DELETE ON purchase_orders BEGIN INSERT INTO settings (key, value) VALUES ('changes/purchase_orders', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_po_lines_added AFTER INSERT ON po_lines BEGIN INSERT INTO settings (key, value) VALUES ('changes/purchase_orders', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_po_lines_changed AFTER UPDATE ON po_lines BEGIN INSERT INTO settings (key, value) VALUES ('changes/purchase_orders', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_po_lines_deleted AFTER DELETE ON po_lines BEGIN INSERT INTO settings (key, value) VALUES ('changes/purchase_orders', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_erp_invoices_added AFTER INSERT ON erp_invoices BEGIN INSERT INTO settings (key, value) VALUES ('changes/register', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_erp_invoices_changed AFTER UPDATE ON erp_invoices BEGIN INSERT INTO settings (key, value) VALUES ('changes/register', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_erp_invoices_deleted AFTER DELETE ON erp_invoices BEGIN INSERT INTO settings (key, value) VALUES ('changes/register', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_feedback_added AFTER INSERT ON feedback BEGIN INSERT INTO settings (key, value) VALUES ('changes/lessons', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_feedback_changed AFTER UPDATE ON feedback BEGIN INSERT INTO settings (key, value) VALUES ('changes/lessons', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_feedback_deleted AFTER DELETE ON feedback BEGIN INSERT INTO settings (key, value) VALUES ('changes/lessons', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_lessons_added AFTER INSERT ON lessons BEGIN INSERT INTO settings (key, value) VALUES ('changes/situations', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_lessons_changed AFTER UPDATE ON lessons BEGIN INSERT INTO settings (key, value) VALUES ('changes/situations', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_lessons_deleted AFTER DELETE ON lessons BEGIN INSERT INTO settings (key, value) VALUES ('changes/situations', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_lesson_approvals_added AFTER INSERT ON lesson_approvals BEGIN INSERT INTO settings (key, value) VALUES ('changes/situations', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_lesson_approvals_changed AFTER UPDATE ON lesson_approvals BEGIN INSERT INTO settings (key, value) VALUES ('changes/situations', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_lesson_approvals_deleted AFTER DELETE ON lesson_approvals BEGIN INSERT INTO settings (key, value) VALUES ('changes/situations', '1') ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_vendor_invoice_added AFTER INSERT ON invoices BEGIN INSERT INTO settings (key, value) SELECT 'changes/vendor/' || NEW.vendor_key, '1' WHERE NEW.vendor_key IS NOT NULL ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_vendor_invoice_changed AFTER UPDATE OF status, export_batch, vendor_key, vendor_name, invoice_number, invoice_date, currency, grand_total, created_at, reviewed_at, final_output, ai_output ON invoices BEGIN INSERT INTO settings (key, value) SELECT 'changes/vendor/' || OLD.vendor_key, '1' WHERE OLD.vendor_key IS NOT NULL ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; INSERT INTO settings (key, value) SELECT 'changes/vendor/' || NEW.vendor_key, '1' WHERE NEW.vendor_key IS NOT NULL AND NEW.vendor_key IS NOT OLD.vendor_key ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;
CREATE TRIGGER changes_vendor_invoice_deleted AFTER DELETE ON invoices BEGIN INSERT INTO settings (key, value) SELECT 'changes/vendor/' || OLD.vendor_key, '1' WHERE OLD.vendor_key IS NOT NULL ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1; END;

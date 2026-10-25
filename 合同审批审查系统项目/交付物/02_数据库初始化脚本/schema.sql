-- 合同审批审查系统 · PostgreSQL 初始化 DDL
-- 由 SQLAlchemy 模型生成，与当前代码一致

CREATE TABLE approval_tasks (
	id SERIAL NOT NULL, 
	approval_code VARCHAR(128) NOT NULL, 
	approval_title VARCHAR(256) NOT NULL, 
	applicant_name VARCHAR(128) NOT NULL, 
	approval_status VARCHAR(32) NOT NULL, 
	task_status VARCHAR(32) NOT NULL, 
	review_requested BOOLEAN NOT NULL, 
	failed_stage VARCHAR(16), 
	retry_count INTEGER NOT NULL, 
	is_standard_version BOOLEAN NOT NULL, 
	is_scanned BOOLEAN NOT NULL, 
	attachment_version INTEGER NOT NULL, 
	write_status VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_approval_tasks_approval_code ON approval_tasks (approval_code);
CREATE INDEX ix_approval_tasks_task_status ON approval_tasks (task_status);

CREATE TABLE review_rules (
	id SERIAL NOT NULL, 
	rule_code VARCHAR(64) NOT NULL, 
	rule_name VARCHAR(128) NOT NULL, 
	rule_category VARCHAR(32) NOT NULL, 
	risk_level VARCHAR(16) NOT NULL, 
	rule_status VARCHAR(16) NOT NULL, 
	match_mode VARCHAR(32) NOT NULL, 
	match_text TEXT, 
	threshold_value NUMERIC(14, 2), 
	target_field VARCHAR(64), 
	operator VARCHAR(8), 
	threshold_unit VARCHAR(16), 
	is_semantic BOOLEAN NOT NULL, 
	semantic_query TEXT, 
	applies_scope VARCHAR(32) NOT NULL, 
	rule_version INTEGER NOT NULL, 
	suggestion_text TEXT NOT NULL, 
	source_page INTEGER, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_review_rules_rule_code ON review_rules (rule_code);
CREATE INDEX ix_review_rules_rule_status ON review_rules (rule_status);

CREATE TABLE approval_attachments (
	id SERIAL NOT NULL, 
	task_id INTEGER NOT NULL, 
	attachment_version INTEGER NOT NULL, 
	file_name VARCHAR(256) NOT NULL, 
	file_type VARCHAR(32) NOT NULL, 
	file_token VARCHAR(128) NOT NULL, 
	file_hash VARCHAR(64) NOT NULL, 
	file_size INTEGER NOT NULL, 
	sort_order INTEGER NOT NULL, 
	minio_path VARCHAR(512) NOT NULL, 
	converted_file_path VARCHAR(512) NOT NULL, 
	download_status VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(task_id) REFERENCES approval_tasks (id)
);
CREATE INDEX ix_approval_attachments_attachment_version ON approval_attachments (attachment_version);
CREATE INDEX ix_approval_attachments_file_hash ON approval_attachments (file_hash);
CREATE INDEX ix_approval_attachments_task_id ON approval_attachments (task_id);

CREATE TABLE comment_logs (
	id SERIAL NOT NULL, 
	task_id INTEGER NOT NULL, 
	attachment_version INTEGER NOT NULL, 
	comment_id VARCHAR(128) NOT NULL, 
	write_status VARCHAR(32) NOT NULL, 
	write_response_text TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(task_id) REFERENCES approval_tasks (id)
);
CREATE INDEX ix_comment_logs_task_id ON comment_logs (task_id);
CREATE INDEX ix_comment_logs_task_version ON comment_logs (task_id, attachment_version);

CREATE TABLE contract_parses (
	id SERIAL NOT NULL, 
	task_id INTEGER NOT NULL, 
	attachment_version INTEGER NOT NULL, 
	merged_pdf_path VARCHAR(512) NOT NULL, 
	full_text TEXT NOT NULL, 
	basic_info_json JSON NOT NULL, 
	clause_info_json JSON NOT NULL, 
	chunks_json JSON NOT NULL, 
	is_superseded BOOLEAN NOT NULL, 
	parse_status VARCHAR(32) NOT NULL, 
	parse_error TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(task_id) REFERENCES approval_tasks (id)
);
CREATE INDEX ix_contract_parses_task_id ON contract_parses (task_id);

CREATE TABLE review_results (
	id SERIAL NOT NULL, 
	task_id INTEGER NOT NULL, 
	attachment_version INTEGER NOT NULL, 
	rules_version INTEGER NOT NULL, 
	overall_risk_level VARCHAR(16) NOT NULL, 
	summary_text TEXT NOT NULL, 
	focus_points_json JSON NOT NULL, 
	comment_text TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_review_result_version UNIQUE (task_id, attachment_version), 
	FOREIGN KEY(task_id) REFERENCES approval_tasks (id)
);
CREATE INDEX ix_review_results_task_id ON review_results (task_id);

CREATE TABLE rule_hits (
	id SERIAL NOT NULL, 
	task_id INTEGER NOT NULL, 
	attachment_version INTEGER NOT NULL, 
	rule_id INTEGER, 
	hit_source VARCHAR(16) NOT NULL, 
	similarity FLOAT, 
	evidence_text TEXT NOT NULL, 
	evidence_position VARCHAR(128) NOT NULL, 
	suggestion_text TEXT NOT NULL, 
	hit_status VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(task_id) REFERENCES approval_tasks (id), 
	FOREIGN KEY(rule_id) REFERENCES review_rules (id)
);
CREATE INDEX ix_rule_hits_task_id ON rule_hits (task_id);

CREATE TABLE task_logs (
	id SERIAL NOT NULL, 
	task_id INTEGER, 
	skill_code VARCHAR(64) NOT NULL, 
	tool_name VARCHAR(64) NOT NULL, 
	input_json JSON, 
	output_json JSON, 
	result_status VARCHAR(16) NOT NULL, 
	duration_ms INTEGER NOT NULL, 
	log_level VARCHAR(16) NOT NULL, 
	log_content TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(task_id) REFERENCES approval_tasks (id)
);
CREATE INDEX ix_task_logs_task_created ON task_logs (task_id, created_at);
CREATE INDEX ix_task_logs_task_id ON task_logs (task_id);

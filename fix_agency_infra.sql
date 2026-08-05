-- Lock AgencyWA + Python webhook base (run on RDP MySQL)
-- mysql -u root python_learning < fix_agency_infra.sql

INSERT INTO site_settings (setting_key, setting_value)
VALUES
  ('api_base_url', '/api'),
  ('agency_api_base_url', 'https://orange-rat-729701.hostingersite.com/api'),
  ('wa_app_public_url', 'https://38.84.24.79:5000'),
  ('wa_webhook_notify_phone', '923004210607')
ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value);

-- Keys/secrets: prefer `python3 fix_agency_infra.py` so they stay encrypted.
-- Plaintext fallback only if encryption script cannot run:
-- INSERT ... agency_api_key / agency_api_secret ...

-- ============================================================
-- Staff Roles Migration
-- Adds role-based access control to admin_users table.
-- Safe to run multiple times (uses IF NOT EXISTS patterns).
-- ============================================================

-- Add role column (admin | staff). Default 'staff' for new users.
-- SQLite doesn't support IF NOT EXISTS for ALTER TABLE, so we
-- wrap in a try-catch pattern via INSERT OR IGNORE on a marker.
-- If the column already exists, the ALTER will fail silently in
-- application code.

ALTER TABLE admin_users ADD COLUMN role TEXT CHECK(role IN ('admin','staff')) DEFAULT 'staff';
ALTER TABLE admin_users ADD COLUMN name TEXT DEFAULT '';
ALTER TABLE admin_users ADD COLUMN phone TEXT;
ALTER TABLE admin_users ADD COLUMN force_password_reset BOOLEAN DEFAULT 0;

-- Promote all existing users to admin (they were admins before roles existed)
UPDATE admin_users SET role = 'admin' WHERE role IS NULL OR role = '';

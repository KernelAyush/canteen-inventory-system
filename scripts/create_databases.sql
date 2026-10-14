-- Run once with psql as your existing PostgreSQL administrator.
-- Abort BEFORE changes if any requested name is already in use.
\set ON_ERROR_STOP on
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'canteen_user')
       OR EXISTS (SELECT 1 FROM pg_database WHERE datname IN ('canteen_inventory', 'canteen_inventory_test')) THEN
        RAISE EXCEPTION 'A requested project role/database already exists. No changes made. Inspect it or choose new names before proceeding.';
    END IF;
END;
$$;
CREATE ROLE canteen_user LOGIN;
\password canteen_user
CREATE DATABASE canteen_inventory OWNER canteen_user;
CREATE DATABASE canteen_inventory_test OWNER canteen_user;

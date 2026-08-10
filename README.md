# Database Migrations Module

This module manages ECS-based database migrations using Alembic.

## Database User Setup

The system uses **two separate database users** for security and separation of concerns:

### 1. Migration User
- **Purpose**: Run database migrations (CREATE, ALTER, DROP tables/columns/indexes)
- **Privileges**: Full schema modification capabilities
- **Used by**: Migration ECS tasks only
- **Trust model**: Controlled through code review and PR process

### 2. Application User
- **Purpose**: Runtime data operations by API/workers
- **Privileges**: SELECT, INSERT, UPDATE, DELETE only (no schema changes)
- **Used by**: Core API, Core Consumer, Voice Processing, Scraping Consumer
- **Trust model**: Untrusted runtime user with minimal privileges

---

## Complete Setup from Scratch

### For Staging Environment

#### Step 1: Connect to RDS as admin user (rappo)

```bash
# Connect to staging RDS instance (connect to 'postgres' database first)
psql -h <staging_rds_host> -U rappo -d postgres
```

#### Step 2: Drop existing database (if starting fresh)

```sql
-- Terminate all connections to the database (if it exists)
SELECT pg_terminate_backend(pg_stat_activity.pid)
FROM pg_stat_activity
WHERE pg_stat_activity.datname = 'myclone_staging_db'
  AND pid <> pg_backend_pid();

-- Drop database (if starting fresh)
DROP DATABASE IF EXISTS myclone_staging_db;

-- Drop users (if they exist)
DROP USER IF EXISTS myclone_staging_migration_user;
DROP USER IF EXISTS myclone_staging_user;
```

#### Step 3: Create database

```sql
-- ============================================================================
-- 1. Create the MyClone database
-- ============================================================================
CREATE DATABASE myclone_staging_db
  WITH OWNER = rappo
  ENCODING = 'UTF8'
  LC_COLLATE = 'en_US.UTF-8'
  LC_CTYPE = 'en_US.UTF-8'
  TABLESPACE = pg_default
  CONNECTION LIMIT = -1;

-- Verify database was created
\l myclone_staging_db
```

#### Step 4: Connect to new database and enable extensions

```sql
-- Connect to the new database
\c myclone_staging_db

-- Enable pgvector extension (required for embeddings)
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
-- Verify extensions
\dx
```

#### Step 5: Create migration user

```sql
-- ============================================================================
-- 2. Create Migration User (full schema modification privileges)
-- ============================================================================
CREATE USER myclone_staging_migration_user WITH PASSWORD '<GENERATE_SECURE_PASSWORD>';

-- Grant basic connection
GRANT CONNECT ON DATABASE myclone_staging_db TO myclone_staging_migration_user;

-- Grant full schema privileges (USAGE + CREATE)
GRANT USAGE, CREATE ON SCHEMA public TO myclone_staging_migration_user;

-- Grant all privileges on existing tables/sequences (none yet, but good for future)
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO myclone_staging_migration_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO myclone_staging_migration_user;

-- Grant future privileges (for objects created during migrations)
-- This ensures any table created by migration user automatically has full privileges
ALTER DEFAULT PRIVILEGES FOR USER myclone_staging_migration_user IN SCHEMA public
  GRANT ALL PRIVILEGES ON TABLES TO myclone_staging_migration_user;
ALTER DEFAULT PRIVILEGES FOR USER myclone_staging_migration_user IN SCHEMA public
  GRANT ALL PRIVILEGES ON SEQUENCES TO myclone_staging_migration_user;

-- Also grant for tables created by admin (rappo) if admin creates tables manually
ALTER DEFAULT PRIVILEGES FOR USER rappo IN SCHEMA public
  GRANT ALL PRIVILEGES ON TABLES TO myclone_staging_migration_user;
ALTER DEFAULT PRIVILEGES FOR USER rappo IN SCHEMA public
  GRANT ALL PRIVILEGES ON SEQUENCES TO myclone_staging_migration_user;

-- Verify user was created
\du myclone_staging_migration_user
```

#### Step 6: Create application user

```sql
-- ============================================================================
-- 3. Create Application User (data operations only)
-- ============================================================================
CREATE USER myclone_staging_user WITH PASSWORD '<GENERATE_SECURE_PASSWORD>';

-- Grant basic connection
GRANT CONNECT ON DATABASE myclone_staging_db TO myclone_staging_user;

-- Grant schema usage only (no CREATE)
GRANT USAGE ON SCHEMA public TO myclone_staging_user;

-- Grant data manipulation privileges on existing tables (none yet)
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO myclone_staging_user;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO myclone_staging_user;

-- Grant future privileges (for tables created by migration user)
-- This is THE KEY: tables created by migration user automatically grant SELECT/INSERT/UPDATE/DELETE to app user
ALTER DEFAULT PRIVILEGES FOR USER myclone_staging_migration_user IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO myclone_staging_user;
ALTER DEFAULT PRIVILEGES FOR USER myclone_staging_migration_user IN SCHEMA public
  GRANT USAGE ON SEQUENCES TO myclone_staging_user;

-- Also grant for admin user (rappo) in case admin creates tables manually
ALTER DEFAULT PRIVILEGES FOR USER rappo IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO myclone_staging_user;
ALTER DEFAULT PRIVILEGES FOR USER rappo IN SCHEMA public
  GRANT USAGE ON SEQUENCES TO myclone_staging_user;

-- Verify user was created
\du myclone_staging_user
```

#### Step 7: Verify setup

```sql
-- Check all users
\du

-- Check database details
\l+ myclone_staging_db

-- Check extensions
\dx

-- Check schema privileges
\dn+

-- Check default privileges (should show grants for future tables)
\ddp
```

#### Step 8: Store passwords in AWS Secrets Manager

Exit psql and run these commands from your terminal:

```bash
# Generate secure passwords (example using openssl)
MIGRATION_PASSWORD=$(openssl rand -base64 32)
APP_PASSWORD=$(openssl rand -base64 32)

# Display passwords (SAVE THESE - you'll need them for the SQL CREATE USER commands above)
echo "=================================================="
echo "Migration user password: $MIGRATION_PASSWORD"
echo "Application user password: $APP_PASSWORD"
echo "=================================================="
echo ""
echo "Use these passwords in the CREATE USER commands above,"
echo "then store them in AWS Secrets Manager with the commands below:"
echo ""

# Store migration user password in Secrets Manager
aws secretsmanager put-secret-value \
  --secret-id myclone-staging-db-migration-password \
  --secret-string "$MIGRATION_PASSWORD" \
  --region us-east-1

# Store application user password in Secrets Manager
aws secretsmanager put-secret-value \
  --secret-id myclone-staging-db-app-password \
  --secret-string "$APP_PASSWORD" \
  --region us-east-1

echo "✅ Passwords stored in AWS Secrets Manager"
```

#### Step 9: Run initial migration to create tables

After users are created and passwords stored in Secrets Manager:

1. Apply Terraform changes (if not already applied)
2. Run migration via GitHub Actions workflow or AWS CLI
3. This will create alembic_version table and all application tables

#### Step 10: Transfer alembic_version ownership (after first migration)

After the first migration runs successfully, transfer ownership:

```sql
-- Connect as admin
\c myclone_staging_db rappo

-- Transfer ownership of alembic_version to migration user
ALTER TABLE alembic_version OWNER TO myclone_staging_migration_user;

-- Verify ownership changed
\dt+ alembic_version

-- Should show: Owner: myclone_staging_migration_user
```

---

### For Production Environment

#### Step 1: Connect to RDS as admin user (rappo)

```bash
# Connect to production RDS instance (connect to 'postgres' database first)
psql -h <production_rds_host> -U rappo -d postgres
```

#### Step 2: Drop existing database (if starting fresh)

```sql
-- Terminate all connections to the database (if it exists)
SELECT pg_terminate_backend(pg_stat_activity.pid)
FROM pg_stat_activity
WHERE pg_stat_activity.datname = 'myclone_production_db'
  AND pid <> pg_backend_pid();

-- Drop database (if starting fresh)
DROP DATABASE IF EXISTS myclone_production_db;

-- Drop users (if they exist)
DROP USER IF EXISTS myclone_production_migration_user;
DROP USER IF EXISTS myclone_production_user;
```

#### Step 3: Create database

```sql
-- ============================================================================
-- 1. Create the MyClone database
-- ============================================================================
CREATE DATABASE myclone_production_db
  WITH OWNER = rappo
  ENCODING = 'UTF8'
  LC_COLLATE = 'en_US.UTF-8'
  LC_CTYPE = 'en_US.UTF-8'
  TABLESPACE = pg_default
  CONNECTION LIMIT = -1;

-- Verify database was created
\l myclone_production_db
```

#### Step 4: Connect to new database and enable extensions

```sql
-- Connect to the new database
\c myclone_production_db

-- Enable pgvector extension (required for embeddings)
CREATE EXTENSION IF NOT EXISTS vector;

-- Verify extensions
\dx
```

#### Step 5: Create migration user

```sql
-- ============================================================================
-- 2. Create Migration User (full schema modification privileges)
-- ============================================================================
CREATE USER myclone_production_migration_user WITH PASSWORD '<GENERATE_SECURE_PASSWORD>';

-- Grant basic connection
GRANT CONNECT ON DATABASE myclone_production_db TO myclone_production_migration_user;

-- Grant full schema privileges (USAGE + CREATE)
GRANT USAGE, CREATE ON SCHEMA public TO myclone_production_migration_user;

-- Grant all privileges on existing tables/sequences
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO myclone_production_migration_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO myclone_production_migration_user;

-- Grant future privileges (for objects created during migrations)
ALTER DEFAULT PRIVILEGES FOR USER myclone_production_migration_user IN SCHEMA public
  GRANT ALL PRIVILEGES ON TABLES TO myclone_production_migration_user;
ALTER DEFAULT PRIVILEGES FOR USER myclone_production_migration_user IN SCHEMA public
  GRANT ALL PRIVILEGES ON SEQUENCES TO myclone_production_migration_user;

-- Also grant for tables created by admin (rappo)
ALTER DEFAULT PRIVILEGES FOR USER rappo IN SCHEMA public
  GRANT ALL PRIVILEGES ON TABLES TO myclone_production_migration_user;
ALTER DEFAULT PRIVILEGES FOR USER rappo IN SCHEMA public
  GRANT ALL PRIVILEGES ON SEQUENCES TO myclone_production_migration_user;

-- Verify user was created
\du myclone_production_migration_user
```

#### Step 6: Create application user

```sql
-- ============================================================================
-- 3. Create Application User (data operations only)
-- ============================================================================
CREATE USER myclone_production_user WITH PASSWORD '<GENERATE_SECURE_PASSWORD>';

-- Grant basic connection
GRANT CONNECT ON DATABASE myclone_production_db TO myclone_production_user;

-- Grant schema usage only (no CREATE)
GRANT USAGE ON SCHEMA public TO myclone_production_user;

-- Grant data manipulation privileges on existing tables
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO myclone_production_user;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO myclone_production_user;

-- Grant future privileges (for tables created by migration user)
ALTER DEFAULT PRIVILEGES FOR USER myclone_production_migration_user IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO myclone_production_user;
ALTER DEFAULT PRIVILEGES FOR USER myclone_production_migration_user IN SCHEMA public
  GRANT USAGE ON SEQUENCES TO myclone_production_user;

-- Also grant for admin user (rappo)
ALTER DEFAULT PRIVILEGES FOR USER rappo IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO myclone_production_user;
ALTER DEFAULT PRIVILEGES FOR USER rappo IN SCHEMA public
  GRANT USAGE ON SEQUENCES TO myclone_production_user;

-- Verify user was created
\du myclone_production_user
```

#### Step 7: Verify setup

```sql
-- Check all users
\du

-- Check database details
\l+ myclone_production_db

-- Check extensions
\dx

-- Check schema privileges
\dn+

-- Check default privileges
\ddp
```

#### Step 8: Store passwords in AWS Secrets Manager

Exit psql and run these commands from your terminal:

```bash
# Generate secure passwords
MIGRATION_PASSWORD=$(openssl rand -base64 32)
APP_PASSWORD=$(openssl rand -base64 32)

# Display passwords (SAVE THESE)
echo "=================================================="
echo "Migration user password: $MIGRATION_PASSWORD"
echo "Application user password: $APP_PASSWORD"
echo "=================================================="

# Store migration user password
aws secretsmanager put-secret-value \
  --secret-id myclone-production-db-migration-password \
  --secret-string "$MIGRATION_PASSWORD" \
  --region us-east-1

# Store application user password
aws secretsmanager put-secret-value \
  --secret-id myclone-production-db-app-password \
  --secret-string "$APP_PASSWORD" \
  --region us-east-1

echo "✅ Passwords stored in AWS Secrets Manager"
```

#### Step 9: Run initial migration

Same as staging - run migration via GitHub Actions after Terraform is applied.

#### Step 10: Transfer alembic_version ownership

```sql
-- Connect as admin
\c myclone_production_db rappo

-- Transfer ownership
ALTER TABLE alembic_version OWNER TO myclone_production_migration_user;

-- Verify
\dt+ alembic_version
```

---

## Permission Model Explanation

### Migration User (Full Schema Modification)
- ✅ **CONNECT** to database
- ✅ **USAGE, CREATE** on schema public
- ✅ **ALL PRIVILEGES** on tables/sequences (includes SELECT, INSERT, UPDATE, DELETE, REFERENCES, TRIGGER, TRUNCATE, ALTER, DROP)
- ✅ Can: CREATE TABLE, ALTER TABLE, DROP TABLE, DROP COLUMN, CREATE INDEX, DROP INDEX
- ✅ Owns all tables created during migrations

### Application User (Data Operations Only)
- ✅ **CONNECT** to database
- ✅ **USAGE** on schema public (no CREATE)
- ✅ **SELECT, INSERT, UPDATE, DELETE** on tables
- ✅ **USAGE** on sequences (for auto-increment columns)
- ❌ Cannot: CREATE, ALTER, DROP, TRUNCATE anything

### Key Security Features

1. **Principle of Least Privilege**: Application runtime has minimal permissions
2. **Separation of Concerns**: Schema changes vs. data operations are separate users
3. **Trust Boundary**: Only reviewed migrations (via PR) can modify schema
4. **Default Privileges**: Future tables automatically grant correct permissions
5. **Audit Trail**: Can track which user made which changes

---

## Verification Commands

After setup, verify permissions are correct:

```sql
-- Connect to database
\c myclone_staging_db

-- Check all users
\du

-- Check database ownership and encoding
\l+ myclone_staging_db

-- Check extensions
\dx

-- Check schema ownership and privileges
\dn+

-- Check default privileges (shows grants for future tables)
\ddp

-- Check specific user privileges on schema
SELECT
  nspname AS schema_name,
  r.rolname AS grantee,
  pg_catalog.has_schema_privilege(r.oid, n.oid, 'CREATE') AS has_create,
  pg_catalog.has_schema_privilege(r.oid, n.oid, 'USAGE') AS has_usage
FROM pg_namespace n
CROSS JOIN pg_roles r
WHERE n.nspname = 'public'
  AND r.rolname IN ('myclone_staging_migration_user', 'myclone_staging_user')
ORDER BY r.rolname;

-- After migrations run, check table ownership and privileges
\dt+

-- Check specific table privileges
\dp

-- Or query privileges directly
SELECT
  grantee,
  privilege_type,
  table_name
FROM information_schema.table_privileges
WHERE table_schema = 'public'
  AND grantee IN ('myclone_staging_migration_user', 'myclone_staging_user')
ORDER BY table_name, grantee;
```

---

## Running Migrations

### Via GitHub Actions (Recommended)

1. Go to: `buildrappo/infrastructure` repository
2. Click: **Actions** → **Database Migrations**
3. Click: **Run workflow**
4. Select environment: `staging` or `production`
5. Click: **Run workflow**

### Via AWS CLI

```bash
# Staging
aws ecs update-service \
  --cluster migrations-staging-cluster \
  --service migrations-staging-service \
  --desired-count 1 \
  --force-new-deployment \
  --region us-east-1

# Production
aws ecs update-service \
  --cluster migrations-production-cluster \
  --service migrations-production-service \
  --desired-count 1 \
  --force-new-deployment \
  --region us-east-1
```

### Check Migration Status

```bash
# View logs
aws ecs describe-services \
  --cluster migrations-staging-cluster \
  --services migrations-staging-service \
  --region us-east-1

# Check task status
aws ecs list-tasks \
  --cluster migrations-staging-cluster \
  --service-name migrations-staging-service \
  --region us-east-1
```

---

## Troubleshooting

### Permission Denied Errors

If migrations fail with `permission denied`:

1. **Check connected to correct database**:
   ```sql
   SELECT current_database(), current_user;
   ```

2. **Verify user exists**:
   ```sql
   \du myclone_staging_migration_user
   ```

3. **Check schema privileges**:
   ```sql
   \dn+
   ```

4. **Check table ownership** (after first migration):
   ```sql
   \dt+ alembic_version
   -- Should show: Owner: myclone_staging_migration_user
   ```

5. **Check default privileges are set**:
   ```sql
   \ddp
   -- Should show ALTER DEFAULT PRIVILEGES for both users
   ```

### Connection Refused / Wrong Host

If migrations can't connect:

1. **Verify RDS endpoint** in Terraform:
   ```bash
   cd infrastructure
   terraform output
   ```

2. **Check environment variables** in ECS task definition:
   ```bash
   aws ecs describe-task-definition \
     --task-definition migrations-staging \
     --region us-east-1
   ```

3. **Verify secrets exist** in Secrets Manager:
   ```bash
   aws secretsmanager get-secret-value \
     --secret-id myclone-staging-db-migration-password \
     --region us-east-1
   ```

### Application Can't Read/Write Data

If API/workers get permission errors:

1. **Check app user has correct privileges** on tables:
   ```sql
   \dp tablename
   -- Should show: myclone_staging_user=arwd (SELECT, INSERT, UPDATE, DELETE)
   ```

2. **Re-apply grants** if tables were created before default privileges were set:
   ```sql
   GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO myclone_staging_user;
   GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO myclone_staging_user;
   ```

### Starting Fresh

To completely reset and start over:

```sql
-- Connect to postgres database
\c postgres

-- Terminate connections
SELECT pg_terminate_backend(pg_stat_activity.pid)
FROM pg_stat_activity
WHERE pg_stat_activity.datname = 'myclone_staging_db'
  AND pid <> pg_backend_pid();

-- Drop everything
DROP DATABASE IF EXISTS myclone_staging_db;
DROP USER IF EXISTS myclone_staging_migration_user;
DROP USER IF EXISTS myclone_staging_user;

-- Now follow the setup steps from the beginning
```

---

## Quick Reference: Copy-Paste Commands

### Staging Setup (All-in-One)

**Step 1: Generate passwords FIRST (in your local terminal)**

```bash
# Generate secure passwords and save them
STAGING_MIGRATION_PASSWORD=$(openssl rand -base64 32)
STAGING_APP_PASSWORD=$(openssl rand -base64 32)

# Display passwords - SAVE THESE!
echo "=================================================="
echo "STAGING PASSWORDS - SAVE THESE NOW!"
echo "=================================================="
echo "Migration user: $STAGING_MIGRATION_PASSWORD"
echo "App user:       $STAGING_APP_PASSWORD"
echo "=================================================="
echo ""
echo "Press Enter after you've saved these passwords..."
read
```

**Step 2: Connect to RDS and run SQL commands**

```bash
# Connect to staging RDS instance
psql -h <staging_rds_host> -U rappo -d postgres
```

**Step 3: Execute SQL commands in psql**

```sql
-- Drop existing (if fresh start)
DROP DATABASE IF EXISTS myclone_staging_db;
DROP USER IF EXISTS myclone_staging_migration_user;
DROP USER IF EXISTS myclone_staging_user;

-- Create database
CREATE DATABASE myclone_staging_db WITH OWNER = rappo ENCODING = 'UTF8';
\c myclone_staging_db
CREATE EXTENSION IF NOT EXISTS vector;

-- Create migration user (replace <PASSWORD> with STAGING_MIGRATION_PASSWORD from step 1)
CREATE USER myclone_staging_migration_user WITH PASSWORD '<PASTE_STAGING_MIGRATION_PASSWORD_HERE>';
GRANT CONNECT ON DATABASE myclone_staging_db TO myclone_staging_migration_user;
GRANT USAGE, CREATE ON SCHEMA public TO myclone_staging_migration_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO myclone_staging_migration_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO myclone_staging_migration_user;
ALTER DEFAULT PRIVILEGES FOR USER myclone_staging_migration_user IN SCHEMA public GRANT ALL PRIVILEGES ON TABLES TO myclone_staging_migration_user;
ALTER DEFAULT PRIVILEGES FOR USER myclone_staging_migration_user IN SCHEMA public GRANT ALL PRIVILEGES ON SEQUENCES TO myclone_staging_migration_user;

-- Create app user (replace <PASSWORD> with STAGING_APP_PASSWORD from step 1)
CREATE USER myclone_staging_user WITH PASSWORD '<PASTE_STAGING_APP_PASSWORD_HERE>';
GRANT CONNECT ON DATABASE myclone_staging_db TO myclone_staging_user;
GRANT USAGE ON SCHEMA public TO myclone_staging_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO myclone_staging_user;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO myclone_staging_user;
ALTER DEFAULT PRIVILEGES FOR USER myclone_staging_migration_user IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO myclone_staging_user;
ALTER DEFAULT PRIVILEGES FOR USER myclone_staging_migration_user IN SCHEMA public GRANT USAGE ON SEQUENCES TO myclone_staging_user;

-- Verify
\du
\l+ myclone_staging_db
\dx
\ddp

-- Exit psql
\q
```

**Step 4: Store passwords in AWS Secrets Manager**

```bash
# Store passwords in AWS (use the variables from step 1)
aws secretsmanager put-secret-value \
  --secret-id myclone-staging-db-migration-password \
  --secret-string "$STAGING_MIGRATION_PASSWORD" \
  --region us-east-1

aws secretsmanager put-secret-value \
  --secret-id myclone-staging-db-app-password \
  --secret-string "$STAGING_APP_PASSWORD" \
  --region us-east-1

echo "✅ Staging database setup complete!"
```

---

### Production Setup (All-in-One)

**Step 1: Generate passwords FIRST (in your local terminal)**

```bash
# Generate secure passwords and save them
PRODUCTION_MIGRATION_PASSWORD=$(openssl rand -base64 32)
PRODUCTION_APP_PASSWORD=$(openssl rand -base64 32)

# Display passwords - SAVE THESE!
echo "=================================================="
echo "PRODUCTION PASSWORDS - SAVE THESE NOW!"
echo "=================================================="
echo "Migration user: $PRODUCTION_MIGRATION_PASSWORD"
echo "App user:       $PRODUCTION_APP_PASSWORD"
echo "=================================================="
echo ""
echo "Press Enter after you've saved these passwords..."
read
```

**Step 2: Connect to RDS and run SQL commands**

```bash
# Connect to production RDS instance
psql -h <production_rds_host> -U rappo -d postgres
```

**Step 3: Execute SQL commands in psql**

```sql
-- Drop existing (if fresh start)
DROP DATABASE IF EXISTS myclone_production_db;
DROP USER IF EXISTS myclone_production_migration_user;
DROP USER IF EXISTS myclone_production_user;

-- Create database
CREATE DATABASE myclone_production_db WITH OWNER = rappo ENCODING = 'UTF8';
\c myclone_production_db
CREATE EXTENSION IF NOT EXISTS vector;

-- Create migration user (replace <PASSWORD> with PRODUCTION_MIGRATION_PASSWORD from step 1)
CREATE USER myclone_production_migration_user WITH PASSWORD '<PASTE_PRODUCTION_MIGRATION_PASSWORD_HERE>';
GRANT CONNECT ON DATABASE myclone_production_db TO myclone_production_migration_user;
GRANT USAGE, CREATE ON SCHEMA public TO myclone_production_migration_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO myclone_production_migration_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO myclone_production_migration_user;
ALTER DEFAULT PRIVILEGES FOR USER myclone_production_migration_user IN SCHEMA public GRANT ALL PRIVILEGES ON TABLES TO myclone_production_migration_user;
ALTER DEFAULT PRIVILEGES FOR USER myclone_production_migration_user IN SCHEMA public GRANT ALL PRIVILEGES ON SEQUENCES TO myclone_production_migration_user;

-- Create app user (replace <PASSWORD> with PRODUCTION_APP_PASSWORD from step 1)
CREATE USER myclone_production_user WITH PASSWORD '<PASTE_PRODUCTION_APP_PASSWORD_HERE>';
GRANT CONNECT ON DATABASE myclone_production_db TO myclone_production_user;
GRANT USAGE ON SCHEMA public TO myclone_production_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO myclone_production_user;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO myclone_production_user;
ALTER DEFAULT PRIVILEGES FOR USER myclone_production_migration_user IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO myclone_production_user;
ALTER DEFAULT PRIVILEGES FOR USER myclone_production_migration_user IN SCHEMA public GRANT USAGE ON SEQUENCES TO myclone_production_user;

-- Verify
\du
\l+ myclone_production_db
\dx
\ddp

-- Exit psql
\q
```

**Step 4: Store passwords in AWS Secrets Manager**

```bash
# Store passwords in AWS (use the variables from step 1)
aws secretsmanager put-secret-value \
  --secret-id myclone-production-db-migration-password \
  --secret-string "$PRODUCTION_MIGRATION_PASSWORD" \
  --region us-east-1

aws secretsmanager put-secret-value \
  --secret-id myclone-production-db-app-password \
  --secret-string "$PRODUCTION_APP_PASSWORD" \
  --region us-east-1

echo "✅ Production database setup complete!"
```

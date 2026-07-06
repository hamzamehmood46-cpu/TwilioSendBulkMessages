-- =============================================================================
-- TwilioSmsConsole — SQL Server Setup Script
-- Engine : Microsoft SQL Server 2025 (compatible with 2019+)
-- Auth   : Windows Authentication (trusted_connection)
-- Run    : sqlcmd -S localhost -E -C -i db_setup.sql
--          or open in SSMS and execute
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Create database (skip if it already exists)
-- -----------------------------------------------------------------------------
IF NOT EXISTS (
    SELECT name FROM sys.databases WHERE name = 'TwilioSmsConsole'
)
BEGIN
    CREATE DATABASE TwilioSmsConsole;
    PRINT 'Database TwilioSmsConsole created.';
END
ELSE
    PRINT 'Database TwilioSmsConsole already exists — skipping.';
GO

USE TwilioSmsConsole;
GO

-- -----------------------------------------------------------------------------
-- 2. message_logs
--    One row per SMS send attempt (sent or failed).
--    sent_at / created_at / updated_at are stored in UTC;
--    the application layer converts them to Eastern Time (EST/EDT) on read.
-- -----------------------------------------------------------------------------
IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_NAME = 'message_logs'
)
BEGIN
    CREATE TABLE message_logs (
        id             INT           NOT NULL IDENTITY(1,1) PRIMARY KEY,
        sent_by        NVARCHAR(100)     NULL,               -- username who triggered the send
        sent_at        DATETIME      NOT NULL DEFAULT GETUTCDATE(),
        created_at     DATETIME      NOT NULL DEFAULT GETUTCDATE(),
        updated_at     DATETIME      NOT NULL DEFAULT GETUTCDATE(),
        from_number    VARCHAR(20)   NOT NULL,               -- Twilio sender number (E.164)
        to_number      VARCHAR(20)   NOT NULL,               -- recipient number  (E.164)
        recipient_name VARCHAR(255)  NOT NULL,
        message_body   VARCHAR(MAX)  NOT NULL,
        status         VARCHAR(20)   NOT NULL,               -- 'sent' | 'failed'
        twilio_sid     VARCHAR(64)       NULL,               -- Twilio message SID (SMxxxxxxx)
        error          VARCHAR(MAX)      NULL                -- error text when status = 'failed'
    );
    PRINT 'Table message_logs created.';
END
ELSE
    PRINT 'Table message_logs already exists — skipping.';
GO

-- -----------------------------------------------------------------------------
-- 3. login_logs
--    One row per authentication event (login success / failure / logout).
-- -----------------------------------------------------------------------------
IF NOT EXISTS (
    SELECT 1 FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_NAME = 'login_logs'
)
BEGIN
    CREATE TABLE login_logs (
        id          INT          NOT NULL IDENTITY(1,1) PRIMARY KEY,
        logged_at   DATETIME     NOT NULL DEFAULT GETUTCDATE(),
        action      VARCHAR(30)  NOT NULL,   -- 'login_success' | 'login_failed' | 'logout'
        ip_address  VARCHAR(45)      NULL,   -- IPv4 or IPv6 of the caller
        details     VARCHAR(MAX)     NULL    -- extra context (e.g. 'user=Hamza')
    );
    PRINT 'Table login_logs created.';
END
ELSE
    PRINT 'Table login_logs already exists — skipping.';
GO

-- -----------------------------------------------------------------------------
-- 4. Verify
-- -----------------------------------------------------------------------------
SELECT
    t.TABLE_NAME,
    c.COLUMN_NAME,
    c.DATA_TYPE,
    c.CHARACTER_MAXIMUM_LENGTH  AS max_len,
    c.IS_NULLABLE
FROM INFORMATION_SCHEMA.TABLES  t
JOIN INFORMATION_SCHEMA.COLUMNS c ON c.TABLE_NAME = t.TABLE_NAME
WHERE t.TABLE_TYPE = 'BASE TABLE'
ORDER BY t.TABLE_NAME, c.ORDINAL_POSITION;
GO

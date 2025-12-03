-- ============================================================
-- AirScout Database Migration: 003_alert_history.sql
-- Tables for tracking sent alerts and preventing duplicates
-- ============================================================
-- Run this AFTER 002_create_tables.sql

-- ============================================================
-- TABLE: alert_history
-- Tracks which hazards have been alerted to which users
-- Used to prevent duplicate notifications
-- ============================================================
CREATE TABLE IF NOT EXISTS alert_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- User who received the alert
    user_id VARCHAR(255) NOT NULL,
    
    -- Subscription that triggered the alert
    subscription_id UUID REFERENCES user_subscriptions(id) ON DELETE CASCADE,
    
    -- Hazard that was alerted
    hazard_source_id VARCHAR(100) NOT NULL,
    
    -- When the alert was sent
    sent_at TIMESTAMPTZ DEFAULT NOW(),
    
    -- Delivery status
    delivery_status VARCHAR(50) DEFAULT 'sent',
    
    -- Error message if delivery failed
    error_message TEXT,
    
    -- Index for fast lookup by user + time
    CONSTRAINT alert_history_user_time_idx UNIQUE (user_id, hazard_source_id, sent_at)
);

-- ============================================================
-- TABLE: push_subscriptions
-- Stores Web Push subscription details for users
-- ============================================================
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- User who owns this subscription
    user_id VARCHAR(255) NOT NULL,
    
    -- Web Push subscription endpoint
    endpoint TEXT NOT NULL UNIQUE,
    
    -- P256DH key for encryption
    p256dh_key TEXT NOT NULL,
    
    -- Auth secret for encryption
    auth_secret TEXT NOT NULL,
    
    -- User agent / device info
    user_agent TEXT,
    
    -- Subscription status
    is_active BOOLEAN DEFAULT TRUE,
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_used_at TIMESTAMPTZ DEFAULT NOW(),
    
    -- Index for user lookup
    CONSTRAINT push_subscriptions_user_idx UNIQUE (user_id, endpoint)
);

-- ============================================================
-- INDEXES for performance
-- ============================================================

-- Fast lookup of recent alerts by user
CREATE INDEX IF NOT EXISTS idx_alert_history_user_sent 
ON alert_history (user_id, sent_at DESC);

-- Fast lookup by hazard
CREATE INDEX IF NOT EXISTS idx_alert_history_hazard 
ON alert_history (hazard_source_id);

-- Active push subscriptions
CREATE INDEX IF NOT EXISTS idx_push_subscriptions_active 
ON push_subscriptions (user_id) WHERE is_active = TRUE;

-- ============================================================
-- FUNCTIONS for alert management
-- ============================================================

-- Function to check if a hazard was recently alerted to a user
CREATE OR REPLACE FUNCTION was_recently_alerted(
    p_user_id VARCHAR(255),
    p_hazard_source_id VARCHAR(100),
    p_hours INTEGER DEFAULT 4
)
RETURNS BOOLEAN AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1 
        FROM alert_history
        WHERE user_id = p_user_id
          AND hazard_source_id = p_hazard_source_id
          AND sent_at > NOW() - (p_hours || ' hours')::INTERVAL
    );
END;
$$ LANGUAGE plpgsql;

-- Function to clean up old alert history
CREATE OR REPLACE FUNCTION cleanup_old_alerts(days_to_keep INTEGER DEFAULT 30)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM alert_history 
    WHERE sent_at < NOW() - (days_to_keep || ' days')::INTERVAL;
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Function to register a push subscription
CREATE OR REPLACE FUNCTION register_push_subscription(
    p_user_id VARCHAR(255),
    p_endpoint TEXT,
    p_p256dh TEXT,
    p_auth TEXT,
    p_user_agent TEXT DEFAULT NULL
)
RETURNS UUID AS $$
DECLARE
    subscription_id UUID;
BEGIN
    INSERT INTO push_subscriptions (user_id, endpoint, p256dh_key, auth_secret, user_agent)
    VALUES (p_user_id, p_endpoint, p_p256dh, p_auth, p_user_agent)
    ON CONFLICT (user_id, endpoint) 
    DO UPDATE SET
        p256dh_key = EXCLUDED.p256dh_key,
        auth_secret = EXCLUDED.auth_secret,
        user_agent = EXCLUDED.user_agent,
        is_active = TRUE,
        last_used_at = NOW()
    RETURNING id INTO subscription_id;
    
    RETURN subscription_id;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- ROW LEVEL SECURITY
-- ============================================================

ALTER TABLE alert_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE push_subscriptions ENABLE ROW LEVEL SECURITY;

-- Users can only see their own alert history
CREATE POLICY "Users see own alerts" ON alert_history
    FOR SELECT
    USING (auth.uid()::text = user_id);

-- Users can only manage their own push subscriptions
CREATE POLICY "Users manage own push subscriptions" ON push_subscriptions
    FOR ALL
    USING (auth.uid()::text = user_id)
    WITH CHECK (auth.uid()::text = user_id);

-- ============================================================
-- COMMENTS
-- ============================================================

COMMENT ON TABLE alert_history IS 'Tracks sent alerts to prevent duplicate notifications';
COMMENT ON TABLE push_subscriptions IS 'Web Push subscription details for sending notifications';
COMMENT ON FUNCTION was_recently_alerted IS 'Check if user was recently alerted about a hazard';
COMMENT ON FUNCTION register_push_subscription IS 'Register or update a Web Push subscription';


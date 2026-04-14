-- ============================================================
-- AirScout Database Migration: 005_enhanced_features.sql
-- Adds AQI hazard type, weather context, and auth-compatible grants
-- ============================================================
-- Run AFTER 004_api_functions.sql

-- 1. Expand hazard type constraint to include AQI
ALTER TABLE hazards_active DROP CONSTRAINT IF EXISTS hazards_active_type_check;
ALTER TABLE hazards_active ADD CONSTRAINT hazards_active_type_check
    CHECK (type IN ('PERMIT', 'TRAFFIC', 'SCHOOL', 'AQI'));

-- 2. Weather context table for wind-adjusted scoring
CREATE TABLE IF NOT EXISTS weather_context (
    city VARCHAR(50) PRIMARY KEY,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    fetched_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE weather_context ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Weather context is publicly readable" ON weather_context
    FOR SELECT USING (true);

-- 3. Grant core RPCs to anon so the PWA works without Supabase Auth.
--    SECURITY DEFINER functions bypass RLS, so the user_id parameter
--    is the only access control. Once Supabase Auth is enabled,
--    tighten these grants back to `authenticated` only.
GRANT EXECUTE ON FUNCTION check_route_hazards TO anon, authenticated;
GRANT EXECUTE ON FUNCTION subscribe_to_route TO anon, authenticated;
GRANT EXECUTE ON FUNCTION get_user_subscriptions TO anon, authenticated;
GRANT EXECUTE ON FUNCTION update_subscription_alerts TO anon, authenticated;
GRANT EXECUTE ON FUNCTION delete_subscription TO anon, authenticated;

-- 4. Composite index for faster alert-cooldown lookups
CREATE INDEX IF NOT EXISTS idx_alert_history_user_hazard_sent
    ON alert_history (user_id, hazard_source_id, sent_at DESC);

-- 5. Partial index for active hazards (most common query)
CREATE INDEX IF NOT EXISTS idx_hazards_active_not_expired
    ON hazards_active (type, severity DESC)
    WHERE expires_at > NOW();

-- 6. Function to get current weather context for scoring
CREATE OR REPLACE FUNCTION get_weather_context(p_city VARCHAR DEFAULT 'chicago')
RETURNS JSONB AS $$
BEGIN
    RETURN (SELECT data FROM weather_context WHERE city = p_city);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

GRANT EXECUTE ON FUNCTION get_weather_context TO anon, authenticated;

COMMENT ON TABLE weather_context IS 'Current weather data for wind-adjusted hazard scoring';
COMMENT ON FUNCTION get_weather_context IS 'Get weather context for hazard scoring adjustments';

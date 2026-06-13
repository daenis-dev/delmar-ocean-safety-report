CREATE TABLE IF NOT EXISTS recreational_activities (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS danger_levels (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS towns (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS tips (
    id SERIAL PRIMARY KEY,
    recreational_activity_id INTEGER NOT NULL REFERENCES recreational_activities(id),
    danger_level_id INTEGER NOT NULL REFERENCES danger_levels(id),
    text TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_tips_activity_danger_text
    ON tips (recreational_activity_id, danger_level_id, text);

CREATE TABLE IF NOT EXISTS wind_readings (
    id BIGSERIAL PRIMARY KEY,
    recorded_at TIMESTAMPTZ NOT NULL,
    town_id INTEGER NOT NULL REFERENCES towns(id),
    current_speed NUMERIC(10, 2) NOT NULL,
    current_direction VARCHAR(20) NOT NULL,
    forecasted_speed_2h NUMERIC(10, 2),
    forecasted_direction_2h VARCHAR(20),
    forecasted_speed_4h NUMERIC(10, 2),
    forecasted_direction_4h VARCHAR(20),
    forecasted_speed_6h NUMERIC(10, 2),
    forecasted_direction_6h VARCHAR(20)
);

CREATE INDEX IF NOT EXISTS ix_wind_readings_recorded_at
    ON wind_readings (recorded_at);

CREATE INDEX IF NOT EXISTS ix_wind_readings_town_id
    ON wind_readings (town_id);

CREATE INDEX IF NOT EXISTS ix_wind_readings_town_recorded_at
    ON wind_readings (town_id, recorded_at DESC);

CREATE TABLE IF NOT EXISTS rain_readings (
    id BIGSERIAL PRIMARY KEY,
    recorded_at TIMESTAMPTZ NOT NULL,
    town_id INTEGER NOT NULL REFERENCES towns(id),
    accumulated_rainfall_past_1_hour NUMERIC(10, 2),
    accumulated_rainfall_past_3_hours NUMERIC(10, 2),
    forecasted_rain_2h NUMERIC(10, 2),
    forecasted_rain_4h NUMERIC(10, 2),
    forecasted_rain_6h NUMERIC(10, 2),
    accumulated_rainfall_past_6_hours NUMERIC(10, 2),
    accumulated_rainfall_past_12_hours NUMERIC(10, 2),
    accumulated_rainfall_past_24_hours NUMERIC(10, 2)
);

CREATE INDEX IF NOT EXISTS ix_rain_readings_recorded_at
    ON rain_readings (recorded_at);

CREATE INDEX IF NOT EXISTS ix_rain_readings_town_id
    ON rain_readings (town_id);

CREATE INDEX IF NOT EXISTS ix_rain_readings_town_recorded_at
    ON rain_readings (town_id, recorded_at DESC);

CREATE TABLE IF NOT EXISTS swell_readings (
    id BIGSERIAL PRIMARY KEY,
    recorded_at TIMESTAMPTZ NOT NULL,
    town_id INTEGER NOT NULL REFERENCES towns(id),
    height NUMERIC(10, 2) NOT NULL,
    period NUMERIC(10, 2),
    direction VARCHAR(20)
);

CREATE INDEX IF NOT EXISTS ix_swell_readings_recorded_at
    ON swell_readings (recorded_at);

CREATE INDEX IF NOT EXISTS ix_swell_readings_town_id
    ON swell_readings (town_id);

CREATE INDEX IF NOT EXISTS ix_swell_readings_town_recorded_at
    ON swell_readings (town_id, recorded_at DESC);

CREATE TABLE IF NOT EXISTS activity_snapshots (
    id BIGSERIAL PRIMARY KEY,
    recreational_activity_id INTEGER NOT NULL REFERENCES recreational_activities(id),
    activity_summary TEXT NOT NULL,
    danger_level_id INTEGER NOT NULL REFERENCES danger_levels(id),
    explanation TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    town_id INTEGER REFERENCES towns(id)
);

CREATE INDEX IF NOT EXISTS ix_activity_snapshots_recorded_at
    ON activity_snapshots (recorded_at);

CREATE INDEX IF NOT EXISTS ix_activity_snapshots_recreational_activity_id
    ON activity_snapshots (recreational_activity_id);

CREATE INDEX IF NOT EXISTS ix_activity_snapshots_danger_level_id
    ON activity_snapshots (danger_level_id);

CREATE INDEX IF NOT EXISTS ix_activity_snapshots_activity_town_recorded_at
    ON activity_snapshots (recreational_activity_id, town_id, recorded_at DESC);

-- =========================================
-- Seed Data: Delmar ocean-only guide
-- =========================================

INSERT INTO recreational_activities (name) VALUES
('Ocean Activities')
ON CONFLICT (name) DO NOTHING;

INSERT INTO danger_levels (name) VALUES
('Safe'),
('Moderately Dangerous'),
('Dangerous'),
('Extremely Dangerous')
ON CONFLICT (name) DO NOTHING;

-- Kept for compatibility with the Kauai Guide schema. No river locations are seeded because this project is ocean-only.

INSERT INTO towns (name) VALUES
('Rehoboth Beach'),
('Bethany Beach'),
('Ocean City')
ON CONFLICT (name) DO NOTHING;

CREATE SCHEMA IF NOT EXISTS canopy AUTHORIZATION canopy_migrator;
CREATE TABLE canopy.events (
    id text PRIMARY KEY,
    station text NOT NULL,
    observed text NOT NULL,
    observed_at timestamptz NOT NULL,
    payload text NOT NULL CHECK (jsonb_typeof(payload::jsonb) = 'object')
);
CREATE INDEX event_station ON canopy.events (station, observed_at DESC, id DESC);
CREATE TABLE canopy.states (
    id text PRIMARY KEY,
    payload text NOT NULL CHECK (jsonb_typeof(payload::jsonb) = 'object')
);
GRANT USAGE ON SCHEMA canopy TO canopy_reader, canopy_writer;
GRANT SELECT ON canopy.events, canopy.states TO canopy_reader, canopy_writer;
GRANT INSERT ON canopy.events TO canopy_writer;
GRANT INSERT, UPDATE ON canopy.states TO canopy_writer;

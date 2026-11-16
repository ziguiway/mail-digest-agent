CREATE TABLE app.runtime_resources (
  key text PRIMARY KEY,
  lock_owner uuid NOT NULL,
  lock_until timestamptz NOT NULL,
  fence integer NOT NULL,
  payload jsonb
);

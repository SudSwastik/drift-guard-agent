-- psql reads the application password from the environment and quotes it as SQL data.
\getenv app_password DRIFT_GUARD_POSTGRES_PASSWORD
CREATE ROLE drift_guard LOGIN PASSWORD :'app_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
ALTER DATABASE drift_guard OWNER TO drift_guard;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO drift_guard;

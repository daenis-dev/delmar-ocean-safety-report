# Delmar Ocean Safety Report

Ocean-only activity safety guide for:

- Rehoboth Beach, DE
- Bethany Beach, DE
- Ocean City, MD

## Modules

- `activitysafety`: Lambda API that returns the latest ocean activity safety snapshot per beach.
- `weatherrefresh`: Lambda job that retrieves weather and marine forecast data, then invokes `weatherupdate`.
- `weatherupdate`: Lambda API/job that stores weather snapshots and creates ocean activity safety snapshots.
- `ui`: Static HTML interface.

## Required environment variables

### activitysafety

- `DB_SECRET_ID`
- `AWS_REGION` default: `us-west-2`
- `DB_SSLMODE` default: `require`
- `DB_CONNECT_TIMEOUT_SECONDS` default: `5`

### weatherrefresh

- `UPDATE_FUNCTION_NAME` default: `delmar-weather-update-api`
- `AWS_REGION` default: `us-west-2`
- `HTTP_TIMEOUT_SECONDS` default: `12`
- `USER_AGENT` optional

### weatherupdate

- `DB_SECRET_ID`
- `AWS_REGION` default: `us-west-2`
- `DB_SSLMODE` default: `require`
- `DB_CONNECT_TIMEOUT_SECONDS` default: `5`

## Setup

1. Create a new Postgres database or schema.
2. Run `weatherupdate/baseline.sql`.
3. Deploy the three Lambda container functions.
4. Set `weatherrefresh.UPDATE_FUNCTION_NAME` to your deployed weather update Lambda name.
5. Update `ui/index.html` and replace `API_URL` with your Activity Safety Lambda Function URL or CloudFront/API domain.

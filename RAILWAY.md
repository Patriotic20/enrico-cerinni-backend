# Railway Configuration

Set the following environment variables in your Railway project settings.

## Required

```
DATABASE_URL        # Auto-injected by the Railway Postgres plugin
JWT_SECRET          # Strong random secret — see below
JWT_REFRESH_SECRET  # Strong random secret — must differ from JWT_SECRET
SECRET_KEY          # Strong random secret
CORS_ORIGIN         # Frontend URL, e.g. https://yourapp.up.railway.app
ENVIRONMENT         # production
DEBUG               # False
```

Generate each secret with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

If `JWT_SECRET` or `JWT_REFRESH_SECRET` is missing — or still set to the
placeholder from `env.example` — the app generates a random key per process and
logs a warning. Tokens stay unforgeable, but every restart signs users out, so
set real values.

## Admin account

```
ADMIN_EMAIL         # Admin login email
ADMIN_USERNAME      # Admin username
ADMIN_PASSWORD      # Only used when the admin does not exist yet
ADMIN_FORCE_RESET   # false; set true for one boot to reset a lost password
```

An existing admin's password is never overwritten on startup, so a password
changed inside the app survives redeploys.

## SMS (Eskiz.uz) and Telegram

These use the nested form with a double underscore — a single underscore is
ignored:

```
NOTIFICATION__SMS_PROVIDER      # eskiz
NOTIFICATION__ESKIZ_EMAIL       # my.eskiz.uz account email
NOTIFICATION__ESKIZ_PASSWORD    # my.eskiz.uz account password
NOTIFICATION__SMS_FROM_NUMBER   # 4546 on a test account, your nickname once approved
NOTIFICATION__TELEGRAM_BOT_TOKEN
```

## Optional

```
SEED_MOCK_DATA      # false. Never enable on a real deployment: the demo rows
                    # cannot be told apart from genuine data afterwards
ACCESS_TOKEN_EXPIRE_MINUTES
REFRESH_TOKEN_EXPIRE_DAYS
BCRYPT_ROUNDS
```

Do not hardcode any of these values in the codebase.

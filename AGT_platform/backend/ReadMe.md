Make sure you are in the **`AGT_platform/backend`** directory for local Python commands (migrations, `app.main`).

1. Install backend dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. If you are using the autograder and not an MIT-affiliated course, consult your school’s IT docs for “OpenID Connect discovery URL” / “Issuer” / “OIDC”, then set **`OIDC_DISCOVERY_URL`** appropriately.

3. **Database migrations (Alembic)** — this repo **already** has `alembic/` and `alembic.ini`. Do **not** run `alembic init` again.

   From **`AGT_platform/backend/`** (so `alembic.ini` is found), with **`DATABASE_URL`** set:

   ```bash
   python -m alembic heads          # should show a single head (e.g. d4e5f6a7b8c9)
   python -m alembic upgrade head
   ```

   Or from the repo root:

   ```bash
   python -m alembic -c AGT_platform/backend/alembic.ini upgrade head
   ```

   With Docker, run migrations **inside** the backend container (see root **`README.md`**).

4. Run the API:

   ```bash
   python -m app.main
   ```

5. Access the backend locally at the host/port your environment configures (see app defaults and `.env`).

**Adding a new migration** (after model changes), from **`backend/`**:

```bash
python -m alembic revision --autogenerate -m "describe_change"
python -m alembic upgrade head
```

Review generated SQL carefully; autogenerate is not always complete.

Make sure you are in **`AGT_platform/frontend`**, then:

1. **Install dependencies**

   ```bash
   npm install
   ```

2. **Start the dev server**

   Host Vite defaults to port **5174** (see `vite.config.ts`) so **Docker** can keep using **5173** for the compose **frontend** service. **`strictPort: false`** allows Vite to try the next free port if **5174** is busy.

   ```bash
   npm run dev
   ```

3. Open the URL printed in the terminal (usually **`http://localhost:5174`**).

---

### OAuth / post-login redirect

The API redirects the browser to **`FRONTEND_BASE_URL`** after Microsoft/Google sign-in. If you use the SPA on **5174**, set in **`AGT_platform/.env`** (or wherever the backend loads env from):

```env
FRONTEND_BASE_URL=http://localhost:5174
```

Then recreate the backend container if you use Docker: `docker compose up -d --force-recreate backend worker`.

---

### Port 5174 already in use

```bash
npm run dev:free-port
npm run dev
```

Or: `npm run dev:local`

**macOS / Linux (manual):**

```bash
lsof -nP -iTCP:5174 -sTCP:LISTEN
kill <PID>
```

---

### Docker frontend on 5173

Compose still maps **`5173:5173`** for the **frontend** container. You do **not** need to stop it to run **`npm run dev`** on the host; they use different ports.


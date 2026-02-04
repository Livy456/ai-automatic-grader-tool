import { useEffect, useMemo, useState } from "react";
import { Button, Container, TextField, Typography, Box, Alert } from "@mui/material";
import { setToken } from "../auth";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:5000";

type DiscoverResponse =
  | { supported: true; domain: string }
  | { supported: false; domain: string; message?: string }
  | { error: string };

export default function Login() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  // If backend returned token in fragment
  useEffect(() => {
    const hash = new URLSearchParams(window.location.hash.replace("#", ""));
    const t = hash.get("token");
    if (t) {
      setToken(t);
      window.location.href = "/"; // go to router landing
    }
  }, []);

  const canSubmit = useMemo(() => email.includes("@") && email.length > 5, [email]);

  async function handleContinue() {
    setErr(null);
    setInfo(null);
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/api/auth/discover`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email }),
        credentials: "include",
      });

      const data = (await res.json()) as DiscoverResponse;

      if (!res.ok) {
        throw new Error("error" in data ? data.error : `discover failed (${res.status})`);
      }

      if ("supported" in data && data.supported) {
        // Start OIDC flow
        window.location.href = `${API_BASE}/api/auth/login?email=${encodeURIComponent(email)}`;
        return;
      }

      if ("supported" in data && !data.supported) {
        setInfo(data.message ?? "Your school is not configured yet.");
        return;
      }

      throw new Error("Unexpected response from server.");
    } catch (e: any) {
      setErr(e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Container maxWidth="sm" sx={{ mt: 10 }}>
      <Typography variant="h4" gutterBottom>
        AI Grader
      </Typography>

      <Typography sx={{ mb: 3, color: "text.secondary" }}>
        Sign in with your college account. Enter your school email and we’ll redirect you to your institution’s SSO.
      </Typography>

      <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <TextField
          label="College email"
          placeholder="name@university.edu"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="email"
          fullWidth
        />

        <Button
          variant="contained"
          disabled={!canSubmit || busy}
          onClick={handleContinue}
        >
          {busy ? "Checking..." : "Continue with SSO"}
        </Button>

        {err && <Alert severity="error">{err}</Alert>}
        {info && <Alert severity="info">{info}</Alert>}

        {/* Optional fallback buttons */}
        <Typography variant="body2" sx={{ mt: 2, color: "text.secondary" }}>
          If your school isn’t configured yet, ask an admin to add it (domain → OIDC discovery URL),
          or use a fallback provider (Google/Microsoft) if you implement one.
        </Typography>
      </Box>
    </Container>
  );
}

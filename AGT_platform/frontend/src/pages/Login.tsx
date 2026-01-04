import { useEffect } from "react";
import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { Button, Container, Typography } from "@mui/material";
import { setToken } from "../auth";

export default function Login() {
  useEffect(() => {
    // token returned in URL fragment: /login#token=...
    const hash = new URLSearchParams(window.location.hash.replace("#", ""));
    const t = hash.get("token");
    if (t) {
      setToken(t);
      window.location.href = "/";
    }
  }, []);

  return (
    <Container sx={{ mt: 8 }}>
      <Typography variant="h4" gutterBottom>AI Grader</Typography>
      <Typography sx={{ mb: 2 }}>
        Login with your college SSO to access your dashboard.
      </Typography>
      <Button variant="contained" href="/api/auth/login">
        Login with College SSO
      </Button>
    </Container>
  );
}

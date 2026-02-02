// src/pages/Login.tsx
import React, { useEffect, useState } from "react";
import { Button, Container, Typography, CircularProgress } from "@mui/material";
import { setToken, getToken } from "../auth";

export default function Login() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Check if the identity provider returned a JWT in the URL fragment:
    // e.g. /login#token=<JWT>&first_login=1
    const params = new URLSearchParams(window.location.hash.replace("#", ""));
    const jwt = params.get("token");
    const first = params.get("first_login"); // "1" if it's their first login
    if (jwt) {
      setLoading(true);
      setToken(jwt); // persist token in localStorage for future API calls

      // If first_login=1, tell backend to record user info (email/course/time/type)
      if (first === "1") {
        fetch("/api/auth/first_login", {
          method: "POST",
          headers: {
            Authorization: `Bearer ${jwt}`,
            "Content-Type": "application/json",
          },
          // backend can derive user info from JWT; body can include additional metadata if needed
          body: JSON.stringify({}),
        })
          .catch((e) => {
            console.error("failed to record first login", e);
          })
          .finally(() => {
            window.location.href = "/";
          });
      } else {
        // not first login, go straight home
        window.location.href = "/";
      }
    }
  }, []);

  return (
    <Container maxWidth="sm" sx={{ mt: 8 }}>
      <Typography variant="h4" gutterBottom>
        AI Grader Login
      </Typography>
      <Typography gutterBottom>
        Sign in with your institution’s Single Sign‑On to access your assignments and grading dashboard.
      </Typography>
      {loading ? (
        <CircularProgress />
      ) : (
        <Button
          variant="contained"
          // this route is provided by the backend; it triggers OIDC auth
          href="/api/auth/login"
        >
          Sign in with College SSO
        </Button>
      )}
      {error && (
        <Typography color="error" sx={{ mt: 2 }}>
          {error}
        </Typography>
      )}
    </Container>
  );
}

import { ReactNode, useEffect, useState } from "react";
import { Box, Drawer, List, ListItemButton, ListItemText, Toolbar, AppBar, Typography, Button } from "@mui/material";
import { api } from "../api";
import { clearToken } from "../auth";

const drawerWidth = 240;

export default function Shell({ children }: { children: ReactNode }) {
  const [role, setRole] = useState<string | null>(null);

  useEffect(() => {
    api.get("/auth/whoami")
      .then(r => setRole(r.data?.user?.role ?? null))
      .catch(() => setRole(null));
  }, []);

  const logout = () => {
    clearToken();
    window.location.href = "/login";
  };

  const nav = [
    { label: "Home", href: "/" },
    ...(role === "teacher" || role === "admin" ? [{ label: "Teacher", href: "/teacher" }] : []),
    ...(role === "admin" ? [{ label: "Admin", href: "/admin" }] : []),
  ];

  return (
    <Box sx={{ display: "flex" }}>
      <AppBar position="fixed" sx={{ zIndex: 1300 }}>
        <Toolbar sx={{ display: "flex", justifyContent: "space-between" }}>
          <Typography variant="h6">AI Grader</Typography>
          <Button color="inherit" onClick={logout}>Logout</Button>
        </Toolbar>
      </AppBar>

      <Drawer
        variant="permanent"
        sx={{
          width: drawerWidth, flexShrink: 0,
          [`& .MuiDrawer-paper`]: { width: drawerWidth, boxSizing: "border-box" }
        }}
      >
        <Toolbar />
        <List>
          {nav.map((n) => (
            <ListItemButton key={n.href} onClick={() => (window.location.href = n.href)}>
              <ListItemText primary={n.label} />
            </ListItemButton>
          ))}
        </List>
      </Drawer>

      <Box component="main" sx={{ flexGrow: 1, p: 3 }}>
        <Toolbar />
        {children}
      </Box>
    </Box>
  );
}

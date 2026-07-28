import { useState, type ReactNode } from "react";
import {
  Outlet,
  useLocation,
  NavLink,
} from "react-router-dom";
import {
  AppBar,
  Avatar,
  Box,
  Chip,
  Divider,
  Drawer,
  IconButton,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Toolbar,
  Tooltip,
  Typography,
  useMediaQuery,
  useTheme,
} from "@mui/material";
import MenuIcon from "@mui/icons-material/Menu";
import AssignmentTurnedInOutlined from "@mui/icons-material/AssignmentTurnedInOutlined";
import AdminPanelSettingsOutlined from "@mui/icons-material/AdminPanelSettingsOutlined";
import AutoFixHighOutlined from "@mui/icons-material/AutoFixHighOutlined";
import NoteAddOutlined from "@mui/icons-material/NoteAddOutlined";
import AssignmentOutlined from "@mui/icons-material/AssignmentOutlined";
import HistoryOutlined from "@mui/icons-material/HistoryOutlined";

const DISPLAY_USER = {
  email: "guest@local",
  role: "teacher",
  initials: "AG",
};

function pageTitle(pathname: string): string {
  if (pathname === "/") return "Dashboard";
  if (pathname === "/grades") return "My Grades";
  if (pathname === "/assignments") return "Submit Assignment";
  if (pathname === "/submissions") return "View Submissions";
  if (pathname === "/autograder") return "Autograder";
  if (pathname === "/autograder/results") return "Autograder Results";
  if (pathname === "/admin") return "Admin Panel";
  if (pathname === "/teacher") return "Teacher";
  if (pathname === "/assignment-creation") return "Assignment Creation";
  if (/^\/autograder\/\d+$/.test(pathname)) return "Autograder Result";
  if (/^\/assignment-creation\/\d+\/review$/.test(pathname)) return "Review Assignment";
  const submit = pathname.match(/^\/assignments\/(\d+)\/submit$/);
  if (submit) return "Submit Assignment";
  const sub = pathname.match(/^\/submissions\/(\d+)$/);
  if (sub) return "Submission Review";
  const ad = pathname.match(/^\/assignments\/([^/]+)$/);
  if (ad) return "Assignment";
  return "AI Grader";
}

type NavItem = {
  label: string;
  to: string;
  icon: ReactNode;
};

const NAV_ITEMS: NavItem[] = [
  {
    label: "Autograder",
    to: "/autograder",
    icon: <AutoFixHighOutlined />,
  },
  {
    label: "Autograder Results",
    to: "/autograder/results",
    icon: <AssignmentTurnedInOutlined />,
  },
  {
    label: "Assignment Creation",
    to: "/assignment-creation",
    icon: <NoteAddOutlined />,
  },
  {
    label: "Submit Assignment",
    to: "/assignments",
    icon: <AssignmentOutlined />,
  },
  {
    label: "View Submissions",
    to: "/submissions",
    icon: <HistoryOutlined />,
  },
  {
    label: "Admin Panel",
    to: "/admin",
    icon: <AdminPanelSettingsOutlined />,
  },
];

export default function Shell() {
  const theme = useTheme();
  const location = useLocation();
  const isMdUp = useMediaQuery(theme.breakpoints.up("md"));
  const isLgUp = useMediaQuery(theme.breakpoints.up("lg"));
  const [mobileOpen, setMobileOpen] = useState(false);

  const narrowNav = isMdUp && !isLgUp;
  const drawerWidth = narrowNav ? 48 : 240;

  const navItems = NAV_ITEMS;
  const title = pageTitle(location.pathname);

  const drawer = (
    <Box sx={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <Toolbar
        sx={{
          minHeight: 64,
          justifyContent: narrowNav ? "center" : "flex-start",
          px: narrowNav ? 0 : 2,
        }}
        aria-label="Application sidebar header"
      >
        {!narrowNav && (
          <Typography
            variant="h6"
            component="div"
            sx={{ fontWeight: 700, color: "primary.main", fontSize: "1.1rem" }}
          >
            AI Grader
          </Typography>
        )}
      </Toolbar>
      <Divider />
      <List
        component="nav"
        sx={{ flex: 1, py: 1 }}
        aria-label="Main navigation"
      >
        {navItems.map((item) => (
          <Tooltip
            key={item.to + item.label}
            title={narrowNav ? item.label : ""}
            placement="right"
          >
            <ListItemButton
              component={NavLink}
              to={item.to}
              end={item.to === "/" ? true : undefined}
              onClick={() => setMobileOpen(false)}
              sx={{
                minHeight: 48,
                px: narrowNav ? 1.5 : 2,
                justifyContent: narrowNav ? "center" : "flex-start",
                borderLeft: "3px solid transparent",
                "&.active": {
                  borderLeftColor: "secondary.main",
                  bgcolor: "rgba(79, 70, 229, 0.08)",
                  "& .MuiListItemIcon-root": { color: "secondary.main" },
                  "& .MuiTypography-root": { fontWeight: 600 },
                },
                "& .MuiListItemIcon-root": { color: "text.secondary" },
                "&.Mui-focusVisible": { outline: "2px solid", outlineOffset: 2 },
              }}
              aria-label={item.label}
            >
              <ListItemIcon
                sx={{
                  minWidth: narrowNav ? 0 : 40,
                  justifyContent: "center",
                }}
              >
                {item.icon}
              </ListItemIcon>
              {!narrowNav && <ListItemText primary={item.label} />}
            </ListItemButton>
          </Tooltip>
        ))}
      </List>
      <Box sx={{ p: narrowNav ? 0.5 : 2, borderTop: 1, borderColor: "divider" }}>
        {!narrowNav && (
          <>
            <Typography variant="caption" color="text.secondary" display="block" noWrap>
              {DISPLAY_USER.email}
            </Typography>
            <Typography variant="caption" color="text.disabled">
              v0.1
            </Typography>
          </>
        )}
        {narrowNav && (
          <Typography variant="caption" color="text.disabled" sx={{ display: "block", textAlign: "center" }}>
            v0.1
          </Typography>
        )}
      </Box>
    </Box>
  );

  return (
    <Box sx={{ display: "flex", minHeight: "100vh", bgcolor: "background.default" }}>
      <AppBar
        position="fixed"
        elevation={0}
        sx={{
          zIndex: (t) => t.zIndex.drawer + 1,
          bgcolor: "background.paper",
          color: "text.primary",
          borderBottom: 1,
          borderColor: "divider",
        }}
      >
        <Toolbar sx={{ minHeight: 64 }}>
          {!isMdUp && (
            <>
              <IconButton
                color="inherit"
                edge="start"
                onClick={() => setMobileOpen(true)}
                sx={{ mr: 1 }}
                aria-label="Open navigation menu"
              >
                <MenuIcon />
              </IconButton>
              <Typography
                variant="h6"
                sx={{ fontWeight: 700, color: "primary.main", mr: 1 }}
                component="span"
              >
                AI Grader
              </Typography>
            </>
          )}
          {isMdUp && (
            <Typography
              variant="h6"
              sx={{ fontWeight: 700, color: "primary.main", mr: 2, minWidth: narrowNav ? 48 : 120 }}
            >
              {narrowNav ? "AG" : "AI Grader"}
            </Typography>
          )}
          <Typography
            component="h1"
            variant="h2"
            sx={{
              flex: 1,
              textAlign: "center",
              fontSize: { xs: "1.1rem", sm: "1.25rem" },
              fontWeight: 600,
            }}
          >
            {title}
          </Typography>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1, ml: 1 }}>
            <Tooltip title={DISPLAY_USER.email}>
              <Avatar
                sx={{ width: 36, height: 36, bgcolor: "secondary.main", fontSize: "0.85rem" }}
                aria-label={`User initials ${DISPLAY_USER.initials}`}
              >
                {DISPLAY_USER.initials}
              </Avatar>
            </Tooltip>
            <Chip
              size="small"
              label={DISPLAY_USER.role}
              color="primary"
              variant="filled"
              aria-label={`Role: ${DISPLAY_USER.role}`}
            />
          </Box>
        </Toolbar>
      </AppBar>

      <Box component="nav" sx={{ width: { md: drawerWidth }, flexShrink: { md: 0 } }}>
        {!isMdUp ? (
          <Drawer
            variant="temporary"
            open={mobileOpen}
            onClose={() => setMobileOpen(false)}
            ModalProps={{ keepMounted: true }}
            sx={{
              display: { xs: "block", md: "none" },
              "& .MuiDrawer-paper": { boxSizing: "border-box", width: 240 },
            }}
          >
            {drawer}
          </Drawer>
        ) : (
          <Drawer
            variant="permanent"
            open
            sx={{
              display: { xs: "none", md: "block" },
              "& .MuiDrawer-paper": {
                boxSizing: "border-box",
                width: drawerWidth,
                borderRight: 1,
                borderColor: "divider",
              },
            }}
          >
            {drawer}
          </Drawer>
        )}
      </Box>

      <Box
        component="main"
        sx={{
          flexGrow: 1,
          width: { md: `calc(100% - ${drawerWidth}px)` },
          minHeight: "100vh",
        }}
      >
        <Toolbar />
        <Box sx={{ p: { xs: 2, md: 3 }, maxWidth: 1400, mx: "auto" }}>
          <Outlet />
        </Box>
      </Box>
    </Box>
  );
}

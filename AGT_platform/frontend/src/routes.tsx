// src/App.tsx or src/routes.tsx
import React from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Login from "./pages/Login";
import StudentDashboard from "./pages/StudentDashboard";
import TeacherDashboard from "./pages/TeacherDashboard";
import AdminDashboard from "./pages/AdminDashboard";
import { getToken } from "./auth";
import jwt_decode from "jwt-decode";

// Define an interface matching the claims in your JWT
interface JwtPayload {
  role: string;
  [key: string]: any;
}

function PrivateRoute({ children }: { children: JSX.Element }) {
  const token = getToken();
  if (!token) {
    // no token → go to login
    return <Navigate to="/login" replace />;
  }
  return children;
}

function RoleBasedDashboard() {
  const token = getToken();
  if (!token) return <Navigate to="/login" replace />;

  const decoded = jwt_decode<JwtPayload>(token);
  const role = decoded.role;

  switch (role) {
    case "admin":
      return <AdminDashboard />;
    case "teacher":
      return <TeacherDashboard />;
    case "student":
    default:
      // default to student if unknown
      return <StudentDashboard />;
  }
}

export default function AppRoutes() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        {/* Protected routes: role-based dashboard */}
        <Route
          path="/*"
          element={
            <PrivateRoute>
              <RoleBasedDashboard />
            </PrivateRoute>
          }
        />
      </Routes>
    </BrowserRouter>
  );
}

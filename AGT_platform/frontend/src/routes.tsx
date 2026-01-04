import { createBrowserRouter } from "react-router-dom";
import Login from "./pages/Login";
import StudentDashboard from "./pages/StudentDashboard";
import AssignmentDetail from "./pages/AssignmentDetail";
import SubmissionUpload from "./pages/SubmissionUpload";
import SubmissionReview from "./pages/SubmissionReview";

export const router = createBrowserRouter([
  { path: "/login", element: <Login /> },
  { path: "/", element: <StudentDashboard /> },
  { path: "/assignments/:id", element: <AssignmentDetail /> },
  { path: "/assignments/:id/submit", element: <SubmissionUpload /> },
  { path: "/submissions/:id", element: <SubmissionReview /> },
]);

import {
  createBrowserRouter,
  createRoutesFromElements,
  Navigate,
  Route,
} from "react-router-dom";
import Shell from "./components/Shell";
import Dashboard from "./pages/Dashboard";
import SubmitAssignment from "./pages/SubmitAssignment";
import SubmissionReview from "./pages/SubmissionReview";
import AssignmentsBrowse from "./pages/AssignmentsBrowse";
import AssignmentDetail from "./pages/AssignmentDetail";
import MyGrades from "./pages/MyGrades";
import SubmissionsList from "./pages/SubmissionsList";
import StandaloneAutograder from "./pages/StandaloneAutograder";
import StandaloneResult from "./pages/StandaloneResult";

const router = createBrowserRouter(
  createRoutesFromElements(
    <Route element={<Shell />}>
      <Route index element={<Navigate to="/autograder" replace />} />
      <Route path="dashboard" element={<Dashboard />} />
      <Route path="admin" element={<Dashboard />} />
      <Route path="teacher" element={<Navigate to="/autograder" replace />} />
      <Route path="grades" element={<MyGrades />} />
      <Route path="assignments" element={<AssignmentsBrowse />} />
      <Route path="assignments/:id" element={<AssignmentDetail />} />
      <Route path="assignments/:id/submit" element={<SubmitAssignment />} />
      <Route path="submissions" element={<SubmissionsList />} />
      <Route path="submissions/:id" element={<SubmissionReview />} />
      <Route path="autograder" element={<StandaloneAutograder />} />
      <Route path="autograder/:id" element={<StandaloneResult />} />
      <Route path="*" element={<Navigate to="/submissions" replace />} />
    </Route>
  ),
);

export default router;
export { router };

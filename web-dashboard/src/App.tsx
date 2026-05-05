import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import AdminPanel from "./pages/AdminPanel";
import InstructorDashboard from "./pages/InstructorDashboard";
import StudentChat from "./pages/StudentChat";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/student" element={<StudentChat />} />
          <Route path="/instructor" element={<InstructorDashboard />} />
          <Route path="/admin" element={<AdminPanel />} />
          <Route path="/" element={<Navigate to="/student" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

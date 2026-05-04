import { Outlet } from "react-router-dom";
import Navigation from "./Navigation";

export default function Layout() {
  return (
    <div className="min-h-screen bg-gray-50">
      <Navigation />
      <main className="max-w-7xl mx-auto">
        <Outlet />
      </main>
    </div>
  );
}

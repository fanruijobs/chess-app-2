import { Route, Routes } from "react-router-dom";

import { RequireAuth } from "./auth/AuthContext";
import { Layout } from "./components/Layout";
import { LoginPage } from "./pages/LoginPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { TrainingSetDetailPage } from "./pages/TrainingSetDetailPage";
import { TrainingSetsPage } from "./pages/TrainingSetsPage";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<TrainingSetsPage />} />
        <Route path="sets/:id" element={<TrainingSetDetailPage />} />
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}

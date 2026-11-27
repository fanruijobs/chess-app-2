import { Route, Routes } from "react-router-dom";

import { RequireAuth } from "./auth/AuthContext";
import { Layout } from "./components/Layout";
import { LoginPage } from "./pages/LoginPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { ReworkPage } from "./pages/ReworkPage";
import { TrainingSessionPage } from "./pages/TrainingSessionPage";
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
        <Route
          path="sets/:id/items/:itemId"
          element={<TrainingSessionPage />}
        />
        <Route path="rework" element={<ReworkPage />} />
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}

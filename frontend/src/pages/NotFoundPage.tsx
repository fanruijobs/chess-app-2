import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <div className="card">
      <h1>Not found</h1>
      <p className="hint">
        This page does not exist in the React app.{" "}
        <Link to="/">Back to training sets</Link>.
      </p>
    </div>
  );
}

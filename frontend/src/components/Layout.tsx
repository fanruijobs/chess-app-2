import { Link, Outlet, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";

export function Layout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  async function handleLogout() {
    await logout();
    navigate("/login");
  }

  return (
    <div className="app-shell">
      <header className="site-header">
        <Link className="brand" to="/">
          Chess Trainer
        </Link>
        <nav className="nav">
          <Link to="/">Training sets</Link>
          <a href="/">Players</a>
          <a href="/analysis/">Analysis</a>
          <a href="/books/">Books</a>
          <a href="/review/">Review</a>
          <a href="/bad-fens/">Bad FENs</a>
          <a href="/rework/">Re-work</a>
          <a href="/train-sets/">Legacy training</a>
        </nav>
        <div className="nav-user">
          <span>{user?.username}</span>
          <button type="button" onClick={handleLogout}>
            Log out
          </button>
        </div>
      </header>
      <main className="container">
        <Outlet />
      </main>
    </div>
  );
}

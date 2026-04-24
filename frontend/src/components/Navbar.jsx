import { Link, useNavigate } from 'react-router-dom';
import { Activity, LogOut, User, CalendarDays } from 'lucide-react';

export default function Navbar() {
  const navigate = useNavigate();
  const token = localStorage.getItem('token');
  const username = localStorage.getItem('username');

  const handleLogout = () => {
    localStorage.removeItem('token');
    localStorage.removeItem('username');
    navigate('/');
  };

  return (
    <nav className="navbar">
      <div className="nav-brand">
        <Link to="/" className="logo-link">
          <span className="logo-icon">🌿</span>
          <span className="logo-text" style={{fontWeight: '900', letterSpacing: '-0.5px'}}>ZenDesk</span>
        </Link>
      </div>
      <div className="nav-links">
        {token ? (
          <>
            <Link to="/dashboard" className="nav-item">
              <Activity size={18} />
              <span>대시보드</span>
            </Link>
            <Link to="/history" className="nav-item">
              <CalendarDays size={18} />
              <span>나의 기록</span>
            </Link>
            <div className="nav-user">
              <User size={18} />
              <span>{username}님</span>
            </div>
            <button onClick={handleLogout} className="btn-logout">
              <LogOut size={18} />
              로그아웃
            </button>
          </>
        ) : (
          <Link to="/login" className="btn-primary">시작하기</Link>
        )}
      </div>
    </nav>
  );
}

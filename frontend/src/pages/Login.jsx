import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';

/* global google */

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID;

export default function Login() {
  const [isLogin, setIsLogin] = useState(true);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  
  const navigate = useNavigate();
  const googleBtnRef = useRef(null);

  // Google 로그인 콜백
  const handleGoogleCallback = async (response) => {
    setError('');
    setLoading(true);

    try {
      const res = await axios.post('http://localhost:8080/api/auth/google', {
        credential: response.credential
      });

      localStorage.setItem('token', res.data.token);
      localStorage.setItem('username', res.data.username);
      if (res.data.profileImage) {
        localStorage.setItem('profileImage', res.data.profileImage);
      }
      navigate('/dashboard');
    } catch (err) {
      if (err.response) {
        setError(err.response.data);
      } else {
        setError('Google 로그인 처리 중 오류가 발생했습니다.');
      }
    } finally {
      setLoading(false);
    }
  };

  // Google 버튼 초기화
  useEffect(() => {
    if (GOOGLE_CLIENT_ID && window.google?.accounts?.id) {
      google.accounts.id.initialize({
        client_id: GOOGLE_CLIENT_ID,
        callback: handleGoogleCallback,
      });
      google.accounts.id.renderButton(googleBtnRef.current, {
        theme: 'outline',
        size: 'large',
        width: '100%',
        text: 'signin_with',
        shape: 'rectangular',
        logo_alignment: 'center',
      });
    }
  }, []);

  // 기존 로그인/회원가입 핸들러
  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const endpoint = isLogin ? '/api/auth/login' : '/api/auth/signup';
      const res = await axios.post(`http://localhost:8080${endpoint}`, {
        username,
        password
      });

      if (isLogin) {
        localStorage.setItem('token', res.data.token);
        localStorage.setItem('username', res.data.username);
        if (res.data.profileImage) {
          localStorage.setItem('profileImage', res.data.profileImage);
        }
        navigate('/dashboard');
      } else {
        alert('회원가입이 완료되었습니다. 로그인해주세요.');
        setIsLogin(true);
        setPassword('');
      }
    } catch (err) {
      if (err.response) {
        setError(err.response.data);
      } else {
        setError('서버와 통신할 수 없습니다.');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="auth-container">
      <div className="auth-card">
        <div className="auth-header">
          <h2>{isLogin ? '로그인' : '회원가입'}</h2>
          <p>{isLogin ? 'ZenDesk 시스템에 오신 것을 환영합니다.' : '새로운 관리 계정을 생성하세요.'}</p>
        </div>

        {error && <div className="alert-error">{error}</div>}

        {/* Google 로그인 버튼 */}
        {isLogin && (
          <div className="google-login-section">
            <div ref={googleBtnRef} className="google-btn-wrapper"></div>
            
            <div className="auth-divider">
              <span className="divider-line"></span>
              <span className="divider-text">또는</span>
              <span className="divider-line"></span>
            </div>
          </div>
        )}

        <form onSubmit={handleSubmit} className="auth-form">
          <div className="form-group">
            <label>아이디</label>
            <input 
              type="text" 
              placeholder="아이디를 입력하세요" 
              value={username}
              onChange={e => setUsername(e.target.value)}
              required 
            />
          </div>
          <div className="form-group">
            <label>비밀번호</label>
            <input 
              type="password" 
              placeholder="비밀번호를 입력하세요" 
              value={password}
              onChange={e => setPassword(e.target.value)}
              required 
            />
          </div>
          
          <button type="submit" className="btn-primary auth-submit" disabled={loading}>
            {loading ? '처리 중...' : (isLogin ? '로그인' : '가입하기')}
          </button>
        </form>

        <div className="auth-toggle">
          {isLogin ? '계정이 없으신가요?' : '이미 계정이 있으신가요?'}
          <button type="button" onClick={() => setIsLogin(!isLogin)} className="btn-text">
            {isLogin ? '회원가입' : '로그인'}
          </button>
        </div>
      </div>
    </div>
  );
}

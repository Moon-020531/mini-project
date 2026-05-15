import { Link } from 'react-router-dom';
import { ShieldCheck, Activity, Zap } from 'lucide-react';

export default function Home() {
  const isLoggedIn = !!localStorage.getItem('token');
  const targetPath = isLoggedIn ? '/dashboard' : '/login';

  return (
    <div className="home-container">
      <header className="hero-section">
        <div className="hero-content">
          <div className="hero-badge">프리미엄 자세 교정 워크스페이스</div>
          <h1 className="hero-title">
            당신의 고요한 몰입을 위한,<br/>
            <span className="highlight-text">가장 스마트한 데스크 솔루션</span>
          </h1>
          <p className="hero-subtitle">
            AI 비전 기술이 실시간으로 척추의 피로도를 분석하고,<br/>
            당신이 깨닫기 전에 건강한 자세로 되돌려줍니다.
          </p>
          <div className="hero-actions">
            <Link to={targetPath} className="btn-primary btn-large">무료로 시작하기</Link>
          </div>
        </div>
        <div className="hero-visual">
          <div className="turtle-illustration" style={{fontSize: '5rem', filter: 'drop-shadow(0 10px 20px rgba(0,0,0,0.1))'}}>
            🐢
          </div>
          <div className="floating-card c1">실시간 자세 피드백</div>
          <div className="floating-card c2">스마트 대시보드</div>
        </div>
      </header>

      <section className="features-section">
        <div className="feature-card">
          <div className="feature-icon"><Activity size={32} color="#00d68f" /></div>
          <h3>AI 랜드마크 분석</h3>
          <p>웹캠 하나로 전방머리자세각도(CVA)를 측정하여 거북목 여부를 판별합니다.</p>
        </div>
        <div className="feature-card">
          <div className="feature-icon"><Zap size={32} color="#ffc75f" /></div>
          <h3>IoT 하드웨어 연동</h3>
          <p>자세가 무너지면 데스크의 LED와 부저가 즉각적인 피드백을 제공합니다.</p>
        </div>
        <div className="feature-card">
          <div className="feature-icon"><ShieldCheck size={32} color="#4dabf7" /></div>
          <h3>데이터 기반 교정</h3>
          <p>10분 단위로 세션을 측정하고, 오늘의 자세 점수를 대시보드로 시각화합니다.</p>
        </div>
      </section>
    </div>
  );
}

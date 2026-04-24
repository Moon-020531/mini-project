import { useState, useEffect, useCallback, useRef } from 'react';
import axios from 'axios';
import { useNavigate } from 'react-router-dom';
import WebcamDetector from '../components/WebcamDetector';

const API_URL = 'http://localhost:8080/api/log';

export default function Dashboard() {
  const [logs, setLogs] = useState([]);
  const [allUsersLogs, setAllUsersLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  
  // 목표 시간 (분 단위)
  const [goalMinutes, setGoalMinutes] = useState(() => {
    const saved = localStorage.getItem('turtleGoalMinutes');
    return saved ? parseInt(saved, 10) : 30;
  });

  // 실시간 세션 타이머 (초 단위)
  const [isCameraActive, setIsCameraActive] = useState(false);
  const [isPresent, setIsPresent] = useState(true); // 자리 비움 감지용
  const [sessionTime, setSessionTime] = useState(0);
  const [showGoalModal, setShowGoalModal] = useState(false); // 목표 달성 축하 모달

  const webcamRef = useRef(null); // WebcamDetector의 내부 메서드 접근용

  const navigate = useNavigate();
  const username = localStorage.getItem('username');

  const fetchLogs = useCallback(async () => {
    if (!username) return;
    try {
      // 1. 내 로그 가져오기
      const res = await axios.get(`${API_URL}?username=${username}`);
      setLogs(res.data);
      
      // 2. 전체 유저 로그 가져오기 (리더보드용)
      const allRes = await axios.get(API_URL);
      setAllUsersLogs(allRes.data);
    } catch (err) {
      console.error('[대시보드] 데이터 조회 실패:', err.message);
    } finally {
      setLoading(false);
    }
  }, [username]);

  useEffect(() => {
    if (!localStorage.getItem('token')) {
      navigate('/login');
      return;
    }
    fetchLogs();
    const interval = setInterval(fetchLogs, 30000);
    return () => clearInterval(interval);
  }, [fetchLogs, navigate]);

  // 실시간 세션 작동 타이머 (목표 시간 도달 시 정지, 자리 비움 시 일시정지)
  useEffect(() => {
    let timer;
    if (isCameraActive) {
      timer = setInterval(() => {
        if (isPresent) {
          setSessionTime(prev => {
            const goalInSeconds = goalMinutes * 60;
            if (prev + 1 === goalInSeconds) {
              setShowGoalModal(true); // 목표 달성 순간 팝업 띄우기
              if (webcamRef.current) {
                webcamRef.current.stopMeasurement(); // AI 카메라도 자동으로 완전 종료
              }
            }
            if (prev >= goalInSeconds) {
              return goalInSeconds;
            }
            return prev + 1;
          });
        }
      }, 1000);
    } else {
      setSessionTime(0);
    }
    return () => clearInterval(timer);
  }, [isCameraActive, isPresent, goalMinutes]);

  const handleGoalChange = (e) => {
    const min = parseInt(e.target.value, 10) || 1; 
    setGoalMinutes(min);
    localStorage.setItem('turtleGoalMinutes', min.toString());
  };

  const todayLogs = logs.filter(log => {
    const logDate = new Date(log.recordTime);
    const today = new Date();
    return logDate.toDateString() === today.toDateString();
  });

  const totalGoodTime = todayLogs.reduce((sum, l) => sum + (l.goodPostureTime || 0), 0);
  const totalWarnings = todayLogs.reduce((sum, l) => sum + (l.warningCount || 0), 0);
  const totalSessions = todayLogs.length;
  const avgGoodPerSession = totalSessions > 0 ? Math.round(totalGoodTime / totalSessions) : 0;

  const formatTime = (seconds) => {
    if (seconds < 60) return `${Math.floor(seconds)}초`;
    const min = Math.floor(seconds / 60);
    const sec = Math.floor(seconds % 60);
    return sec > 0 ? `${min}분 ${sec}초` : `${min}분`;
  };

  // 목표 달성률 계산
  const goalInSeconds = goalMinutes * 60;
  const progressPercent = Math.min((sessionTime / goalInSeconds) * 100, 100) || 0;
  const isGoalReached = progressPercent >= 100;

  // ==== 리더보드 계산 로직 ====
  const todayAllLogs = allUsersLogs.filter(log => {
    const logDate = new Date(log.recordTime);
    return logDate.toDateString() === new Date().toDateString();
  });
  
  const userRankings = Array.from(
    todayAllLogs.reduce((acc, log) => {
      const u = log.username || '익명';
      if (!acc.has(u)) acc.set(u, 0);
      acc.set(u, acc.get(u) + (log.goodPostureTime || 0));
      return acc;
    }, new Map())
  )
    .sort((a, b) => b[1] - a[1]) // 내림차순 정렬
    .slice(0, 5); // TOP 5 추출

  if (loading) {
    return <div className="loading-state"><div className="loading-spinner" />대시보드 데이터 로딩...</div>;
  }

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1>{username}님의 워크스페이스 ✨</h1>
        <p>나만의 프리미엄 인체공학 모니터링 룸</p>
      </header>
      
      {/* 실시간 모니터링 및 사이드 위젯 그리드 */}
      <section className="main-content-grid">
        
        {/* 좌측: AI 웹캠 (메인 뷰포트 영역 보장) */}
        <div className="webcam-column">
          <WebcamDetector 
            ref={webcamRef}
            username={username} 
            onDataSaved={fetchLogs} 
            onMeasuringChange={setIsCameraActive} 
            onPresenceChange={setIsPresent}
          />
        </div>
        
        {/* 우측: 사이드 패널 (포커스 타이머 & 가이드) */}
        <div className="side-panel-column">
          
          {/* 1. 애니메이션 빵빵한 라이브 포커스 타이머 (사이드바 배치로 한눈에 보임) */}
          <div className="animated-focus-widget">
            <div className="afw-bg-glow"></div>
            
            <div className="afw-header">
              <h2 className="afw-title">🌿 Focus Session</h2>
              <span className="afw-subtitle">
                {isCameraActive ? (
                  isPresent ? (
                    <span style={{color: '#10b981', display: 'flex', alignItems: 'center', gap: '6px', justifyContent: 'center'}}>
                      <span className="ping-dot"><span className="ping-anim"></span><span className="ping-core"></span></span>
                      측정 중
                    </span>
                  ) : (
                    <span style={{color: '#64748b', display: 'flex', alignItems: 'center', gap: '6px', justifyContent: 'center'}}>
                      🈳 자리 비움 (일시 정지)
                    </span>
                  )
                ) : '카메라 대기 중'}
              </span>
            </div>

            <div className="afw-timer-display">
              <span className="afw-time">{formatTime(sessionTime)}</span>
            </div>

            {/* 움직이는 진짜 '길(Road)' 디자인이 적용된 트랙 */}
            <div className={`afw-track-wrapper ${isCameraActive ? 'active' : 'idle'}`}>
              <div className="afw-track-bg">
                <div 
                  className="afw-track-fill" 
                  style={{ width: `${progressPercent}%` }}
                ></div>
                <div 
                  className={`afw-runner ${isCameraActive ? 'active' : 'idle'}`}
                  style={{ left: `calc(${progressPercent}% - ${progressPercent > 95 ? 28 : 0}px)` }}
                >
                  <div className="afw-runner-inner"></div>
                  <img 
                    src="https://raw.githubusercontent.com/microsoft/fluentui-emoji/main/assets/Turtle/3D/turtle_3d.png" 
                    alt="Cute 3D Turtle" 
                    className="afw-runner-img" 
                  />
                </div>
                
                {/* 트랙의 도착 지점 거북이 먹이 */}
                <div className="afw-finish-flag">
                  🥬
                </div>
              </div>
            </div>

            <div className="afw-controls">
              <div>달성률 <strong>{progressPercent.toFixed(1)}%</strong></div>
              <div style={{display:'flex', alignItems:'center', gap:'6px'}}>
                목표
                <input 
                  type="number" 
                  value={goalMinutes} 
                  onChange={handleGoalChange} 
                  min="1" max="999" 
                  className="afw-goal-input" 
                />
                분
              </div>
            </div>
            
            {isGoalReached && (
              <div className="afw-celebration">
                🎉 와우! 목표를 정복했어요! 🎉
              </div>
            )}
          </div>
          
          {/* 2. 오늘의 거북목 경고 누적 위젯 (한눈에 경고 상태 파악) */}
          <div className={`animated-warning-widget ${totalWarnings >= 5 ? 'danger' : totalWarnings >= 2 ? 'warning' : 'safe'}`}>
            <div className="aww-bg-glow"></div>
            <div className="aww-content">
              <div className="aww-header">
                <span className="aww-icon">{totalWarnings >= 5 ? '🚨' : totalWarnings >= 2 ? '⚠️' : '🛡️'}</span>
                <h3>오늘의 거북목 경고 누적</h3>
              </div>
              <div className="aww-body">
                <div className="aww-number">
                  {totalWarnings}<span className="aww-unit">회</span>
                </div>
                <p className="aww-text">
                  {totalWarnings === 0 ? '✨ 아주 훌륭해요! 완벽한 바른 자세 유지 중!' :
                   totalWarnings < 2 ? '💡 자세가 앞쪽으로 가끔 쏠리고 있어요.' :
                   totalWarnings < 5 ? '🔥 주의! 현재 확실한 거북목 진행 구간입니다.' : 
                   '🚨 심각한 자세 붕괴! 즉시 목 스트레칭을 하세요!'}
                </p>
              </div>
            </div>
          </div>
          
        </div>
      </section>

      {/* 모니터링 통계 숫자 요약 판 */}
      <section className="stats-grid" style={{marginTop: '40px'}}>
        <div className="stat-card green"><div className="label">바른 자세 유지</div><div className="value">{formatTime(totalGoodTime)}</div></div>
        <div className="stat-card red"><div className="label">거북목 경고 누적</div><div className="value">{totalWarnings}회</div></div>
        <div className="stat-card blue"><div className="label">총 측정 세션</div><div className="value">{totalSessions}회</div></div>
        <div className="stat-card purple"><div className="label">세션당 평균 유지</div><div className="value">{formatTime(avgGoodPerSession)}</div></div>
      </section>

      {/* 목표 달성 축하 팝업 모달 */}
      {showGoalModal && (
        <div className="goal-modal-overlay fade-in">
          <div className="goal-modal-content">
            <div className="goal-modal-icon">🎉</div>
            <h2 className="goal-modal-title">Focus Session 목표 달성!</h2>
            <p className="goal-modal-text">
              대단해요! 설정하신 <strong>{goalMinutes}분</strong> 동안<br/>
              바른 자세로 성공적인 집중을 이루어냈습니다 🌿
            </p>
            <div className="goal-modal-actions">
              <button 
                className="btn-primary" 
                onClick={() => setShowGoalModal(false)}
                style={{ width: '100%', padding: '14px', fontSize: '16px' }}
              >
                닫기
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}

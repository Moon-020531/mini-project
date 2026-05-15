import { useState, useEffect, useCallback, useRef } from 'react';
import axios from 'axios';
import { useNavigate } from 'react-router-dom';
import WebcamDetector from '../components/WebcamDetector';

const API_URL = 'http://localhost:8080/api/log';

export default function Dashboard() {
  const [logs, setLogs] = useState([]);
  const [allUsersLogs, setAllUsersLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  
  const [goalMinutes, setGoalMinutes] = useState(() => {
    const saved = localStorage.getItem('turtleGoalMinutes');
    return saved ? parseInt(saved, 10) : 30;
  });

  const [isCameraActive, setIsCameraActive] = useState(false);
  const [isPresent, setIsPresent] = useState(true);
  const [sessionTime, setSessionTime] = useState(0);
  const [showGoalModal, setShowGoalModal] = useState(false);
  const [postureData, setPostureData] = useState(null);

  const webcamRef = useRef(null);
  const navigate = useNavigate();
  const username = localStorage.getItem('username');

  const fetchLogs = useCallback(async () => {
    if (!username) return;
    try {
      const res = await axios.get(`${API_URL}?username=${username}`);
      setLogs(res.data);
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

  useEffect(() => {
    let timer;
    if (isCameraActive) {
      timer = setInterval(() => {
        if (isPresent) {
          setSessionTime(prev => {
            const goalInSeconds = goalMinutes * 60;
            if (prev + 1 === goalInSeconds) {
              setShowGoalModal(true);
              if (webcamRef.current) webcamRef.current.stopMeasurement();
            }
            if (prev >= goalInSeconds) return goalInSeconds;
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
    return logDate.toDateString() === new Date().toDateString();
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

  const goalInSeconds = goalMinutes * 60;
  const progressPercent = Math.min((sessionTime / goalInSeconds) * 100, 100) || 0;
  const isGoalReached = progressPercent >= 100;

  const getScoreColor = (score) => {
    if (score >= 80) return '#10b981';
    if (score >= 60) return '#fbbf24';
    return '#ef4444';
  };
  const getScoreLabel = (score) => {
    if (score >= 80) return '우수';
    if (score >= 60) return '주의';
    return '위험';
  };

  if (loading) {
    return <div className="loading-state"><div className="loading-spinner" />대시보드 데이터 로딩...</div>;
  }

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1>{username}님의 워크스페이스 ✨</h1>
        <p>나만의 프리미엄 인체공학 모니터링 룸</p>
      </header>

      {/* ===== 1. 상단: 오늘의 통계 요약 ===== */}
      <section className="stats-grid">
        <div className="stat-card green"><div className="label">바른 자세 유지</div><div className="value">{formatTime(totalGoodTime)}</div></div>
        <div className="stat-card red"><div className="label">거북목 경고</div><div className="value">{totalWarnings}회</div></div>
        <div className="stat-card blue"><div className="label">측정 세션</div><div className="value">{totalSessions}회</div></div>
        <div className="stat-card purple"><div className="label">세션 평균</div><div className="value">{formatTime(avgGoodPerSession)}</div></div>
      </section>

      {/* ===== 2. 중앙: 웹캠 + 포커스 타이머 ===== */}
      <section className="main-content-grid">
        <div className="webcam-column">
          <WebcamDetector 
            ref={webcamRef}
            username={username} 
            onDataSaved={fetchLogs} 
            onMeasuringChange={setIsCameraActive} 
            onPresenceChange={setIsPresent}
            onPostureData={setPostureData}
          />
        </div>
        
        <div className="side-panel-column">
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
                      자리 비움 (일시 정지)
                    </span>
                  )
                ) : '카메라 대기 중'}
              </span>
            </div>
            <div className="afw-timer-display">
              <span className="afw-time">{formatTime(sessionTime)}</span>
            </div>
            <div className={`afw-track-wrapper ${isCameraActive ? 'active' : 'idle'}`}>
              <div className="afw-track-bg">
                <div className="afw-track-fill" style={{ width: `${progressPercent}%` }}></div>
                <div 
                  className={`afw-runner ${isCameraActive ? 'active' : 'idle'}`}
                  style={{ left: `calc(${progressPercent}% - ${progressPercent > 95 ? 28 : 0}px)` }}
                >
                  <div className="afw-runner-inner"></div>
                  <img 
                    src="https://raw.githubusercontent.com/microsoft/fluentui-emoji/main/assets/Turtle/3D/turtle_3d.png" 
                    alt="Cute 3D Turtle" className="afw-runner-img" 
                  />
                </div>
                <div className="afw-finish-flag">🥬</div>
              </div>
            </div>
            <div className="afw-controls">
              <div>달성률 <strong>{progressPercent.toFixed(1)}%</strong></div>
              <div style={{display:'flex', alignItems:'center', gap:'6px'}}>
                목표
                <input type="number" value={goalMinutes} onChange={handleGoalChange} min="1" max="999" className="afw-goal-input" />
                분
              </div>
            </div>
            {isGoalReached && (
              <div className="afw-celebration">🎉 와우! 목표를 정복했어요! 🎉</div>
            )}
          </div>

          {/* 실시간 자세 점수 */}
          <div className={`posture-score-widget ${postureData ? 'active' : ''}`}>
            <div className="psw-header">
              <span className="psw-icon">🎯</span>
              <h3>실시간 자세 점수</h3>
            </div>
            {postureData ? (
              <div className="psw-body">
                <div className="psw-score-ring" style={{borderColor: getScoreColor(postureData.postureScore)}}>
                  <span className="psw-score-value" style={{color: getScoreColor(postureData.postureScore)}}>
                    {postureData.postureScore}
                  </span>
                  <span className="psw-score-label">{getScoreLabel(postureData.postureScore)}</span>
                </div>
                <div className="psw-breakdown">
                  <div className="psw-metric">
                    <span className="psw-metric-label">🦴 거북목 (CVA)</span>
                    <div className="psw-metric-bar-wrap">
                      <div className="psw-metric-bar" style={{width: `${postureData.scoreCva}%`, background: postureData.scoreCva >= 70 ? '#10b981' : postureData.scoreCva >= 40 ? '#fbbf24' : '#ef4444'}}></div>
                    </div>
                    <span className="psw-metric-value">{postureData.cvaAngle}°</span>
                  </div>
                  <div className="psw-metric">
                    <span className="psw-metric-label">🙆 어깨 균형</span>
                    <div className="psw-metric-bar-wrap">
                      <div className="psw-metric-bar" style={{width: `${postureData.scoreShoulder}%`, background: postureData.scoreShoulder >= 70 ? '#10b981' : postureData.scoreShoulder >= 40 ? '#fbbf24' : '#ef4444'}}></div>
                    </div>
                    <span className="psw-metric-value">{postureData.shoulderTilt}°</span>
                  </div>
                  <div className="psw-metric">
                    <span className="psw-metric-label">📏 모니터 거리</span>
                    <div className="psw-metric-bar-wrap">
                      <div className="psw-metric-bar" style={{width: `${postureData.scoreDistance}%`, background: postureData.scoreDistance >= 70 ? '#10b981' : postureData.scoreDistance >= 40 ? '#fbbf24' : '#ef4444'}}></div>
                    </div>
                    <span className="psw-metric-value">{postureData.distanceCm > 0 ? `${postureData.distanceCm}cm` : '-'}</span>
                  </div>
                </div>
              </div>
            ) : (
              <div className="psw-empty"><p>카메라를 켜면 자세 분석이 시작됩니다</p></div>
            )}
          </div>

          {/* 눈 피로도 */}
          <div className={`eye-fatigue-widget ${postureData?.isFatigued ? 'fatigued' : ''}`}>
            <div className="efw-header">
              <span className="efw-icon">{postureData?.isFatigued ? '😵' : '👁️'}</span>
              <h3>눈 피로도 모니터</h3>
            </div>
            {postureData ? (
              <div className="efw-body">
                <div className="efw-stats-row">
                  <div className="efw-stat">
                    <span className="efw-stat-label">EAR 수치</span>
                    <span className="efw-stat-value">{postureData.ear}</span>
                  </div>
                  <div className="efw-stat">
                    <span className="efw-stat-label">분당 깜빡임</span>
                    <span className={`efw-stat-value ${postureData.blinksPerMin < 15 ? 'danger' : 'safe'}`}>
                      {postureData.blinksPerMin}회
                    </span>
                  </div>
                </div>
                {postureData.isFatigued && (
                  <div className="efw-alert">⚠️ 피로도 누적: 인공눈물 점안 및 휴식 권장</div>
                )}
                {postureData.isTooClose && (
                  <div className="efw-alert warning">📏 모니터와 너무 가깝습니다! (권장: 40cm 이상)</div>
                )}
                {postureData.isShoulderAsymmetry && (
                  <div className="efw-alert warning">🙆 어깨 비대칭 감지! 자세를 교정해 주세요.</div>
                )}
              </div>
            ) : (
              <div className="efw-empty"><p>측정 대기 중...</p></div>
            )}
          </div>

        </div>
      </section>

      {/* 목표 달성 모달 */}
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
              <button className="btn-primary" onClick={() => setShowGoalModal(false)}
                style={{ width: '100%', padding: '14px', fontSize: '16px' }}>
                닫기
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

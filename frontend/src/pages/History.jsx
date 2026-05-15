import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { CalendarDays, AlertTriangle, Clock, ListChecks, ChevronLeft, ChevronRight, Target } from 'lucide-react';

export default function History() {
  const navigate = useNavigate();
  const [dataMap, setDataMap] = useState({});
  const [loading, setLoading] = useState(true);
  
  // 달력 뷰 달력 기준 (연, 월)
  const [currentDate, setCurrentDate] = useState(new Date());
  // 유저가 클릭한 선택된 날짜
  const [selectedDate, setSelectedDate] = useState(new Date());

  useEffect(() => {
    const token = localStorage.getItem('token');
    const username = localStorage.getItem('username');

    if (!token) {
      navigate('/login');
      return;
    }

    const fetchLogs = async () => {
      try {
        const res = await fetch(`http://localhost:8080/api/log?username=${username}`);
        if (!res.ok) throw new Error('Failed to fetch logs');
        const logs = await res.json();
        
        // 날짜별로 맵핑 "YYYY-MM-DD" -> { stats }
        const grouped = logs.reduce((acc, log) => {
          if (!log.recordTime) return acc;
          const dateStr = log.recordTime.substring(0, 10);
          
          if (!acc[dateStr]) {
            acc[dateStr] = { date: dateStr, sessions: 0, totalGoodTime: 0, totalWarnings: 0, sumScore: 0, scoreCount: 0 };
          }
          acc[dateStr].sessions += 1;
          acc[dateStr].totalGoodTime += (log.goodPostureTime || 0);
          acc[dateStr].totalWarnings += (log.warningCount || 0);
          
          if (log.postureScore !== null && log.postureScore !== undefined) {
            acc[dateStr].sumScore += log.postureScore;
            acc[dateStr].scoreCount += 1;
          }

          if (acc[dateStr].scoreCount > 0) {
            // DB에 실시간 점수가 있는 최신 데이터
            acc[dateStr].postureScore = Math.round(acc[dateStr].sumScore / acc[dateStr].scoreCount);
          } else {
            // DB 점수가 없는 예전 데이터 (추정)
            const avgWarns = acc[dateStr].totalWarnings / acc[dateStr].sessions;
            acc[dateStr].postureScore = Math.max(0, Math.round(100 - avgWarns * 15));
          }
          return acc;
        }, {});

        setDataMap(grouped);
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    };

    fetchLogs();
  }, [navigate]);

  const formatTime = (seconds) => {
    const rootMin = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${rootMin}분 ${secs}초`;
  };

  const getDayOfWeek = (date) => {
    const days = ['일', '월', '화', '수', '목', '금', '토'];
    return days[date.getDay()] + '요일';
  };

  // 달력 렌더링에 필요한 날짜 수학식
  const year = currentDate.getFullYear();
  const month = currentDate.getMonth(); // 0-based
  
  const firstDayOfMonth = new Date(year, month, 1).getDay(); // 0(일) ~ 6(토)
  const daysInMonth = new Date(year, month + 1, 0).getDate(); // 해당 월의 마지막 일 수

  // 달력 이전달/다음달 이동
  const handlePrevMonth = () => setCurrentDate(new Date(year, month - 1, 1));
  const handleNextMonth = () => setCurrentDate(new Date(year, month + 1, 1));
  
  // YYYY-MM-DD 포맷 도우미 함수
  const toDateString = (y, m, d) => {
    return `${y}-${String(m + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
  };

  const selectedDateStr = toDateString(selectedDate.getFullYear(), selectedDate.getMonth(), selectedDate.getDate());
  const selectedData = dataMap[selectedDateStr];

  const calendarDays = [];
  // 앞쪽 빈 칸 채우기
  for (let i = 0; i < firstDayOfMonth; i++) {
    calendarDays.push(<div key={`empty-${i}`} className="calendar-cell empty"></div>);
  }
  // 실제 날짜들 채우기
  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = toDateString(year, month, d);
    const hasData = !!dataMap[dateStr];
    const isSelected = dateStr === selectedDateStr;
    const isToday = dateStr === toDateString(new Date().getFullYear(), new Date().getMonth(), new Date().getDate());

    calendarDays.push(
      <div 
        key={`day-${d}`} 
        className={`calendar-cell ${isSelected ? 'selected' : ''} ${isToday ? 'today' : ''} ${hasData ? 'has-data' : ''}`}
        onClick={() => setSelectedDate(new Date(year, month, d))}
      >
        <span className="cell-number">{d}</span>
        {hasData && <span className="cell-dot">🌱</span>}
      </div>
    );
  }

  if (loading) {
    return (
      <div style={{display:'flex', justifyContent:'center', padding:'100px', color:'var(--text-secondary)'}}>
        <h2>기록을 불러오는 중입니다... 🌱</h2>
      </div>
    );
  }

  return (
    <div className="history-container fade-in">
      <div className="history-header">
        <div className="header-title">
          <CalendarDays size={32} color="var(--accent-green)" />
          <h2>나의 집중 성장 일지</h2>
        </div>
        <p>꾸준한 자세 기록이 건강한 몸을 만듭니다.</p>
      </div>

      <div className="history-layout">
        <div className="calendar-panel">
          <div className="calendar-nav">
            <button onClick={handlePrevMonth} className="btn-nav"><ChevronLeft size={24} /></button>
            <h3 className="calendar-title">{year}년 {month + 1}월</h3>
            <button onClick={handleNextMonth} className="btn-nav"><ChevronRight size={24} /></button>
          </div>
          <div className="calendar-grid">
            <div className="weekday-header red">일</div>
            <div className="weekday-header">월</div>
            <div className="weekday-header">화</div>
            <div className="weekday-header">수</div>
            <div className="weekday-header">목</div>
            <div className="weekday-header">금</div>
            <div className="weekday-header blue">토</div>
            {calendarDays}
          </div>
        </div>

        <div className="detail-panel">
          <div className="detail-panel-header">
            <h3>{selectedDate.getMonth() + 1}월 {selectedDate.getDate()}일 기록</h3>
            <span className="detail-badge">{getDayOfWeek(selectedDate)}</span>
          </div>

           {selectedData ? (
             <div className="detail-stats-card">
               {/* 자세 평균 점수 헤더 */}
               <div className="posture-score-banner" style={{
                 background: selectedData.postureScore >= 80 ? 'linear-gradient(135deg, #10b981, #059669)' : selectedData.postureScore >= 60 ? 'linear-gradient(135deg, #f59e0b, #d97706)' : 'linear-gradient(135deg, #ef4444, #b91c1c)',
                 borderRadius: '20px', padding: '20px 24px', marginBottom: '16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', color: 'white'
               }}>
                 <div>
                   <div style={{fontSize: '13px', fontWeight: 700, opacity: 0.85, marginBottom: '4px'}}>자세 평균 점수</div>
                   <div style={{fontSize: '36px', fontWeight: 900, letterSpacing: '-2px', lineHeight: 1}}>
                     {selectedData.postureScore}<span style={{fontSize: '18px', fontWeight: 700, opacity: 0.7, marginLeft: '2px'}}>점</span>
                   </div>
                 </div>
                 <Target size={36} style={{opacity: 0.4}} />
               </div>

               <div className="card-stats">
                 <div className="stat-item green-stat">
                   <div className="stat-icon"><Clock size={24} /></div>
                   <div className="stat-info">
                     <span className="stat-label">총 바른 자세 유지시간</span>
                     <span className="stat-value">{formatTime(selectedData.totalGoodTime)}</span>
                   </div>
                 </div>
                 
                 <div className="stat-item red-stat">
                   <div className="stat-icon"><AlertTriangle size={24} /></div>
                   <div className="stat-info">
                     <span className="stat-label">거북목 경고 누적</span>
                     <span className="stat-value">{selectedData.totalWarnings}회</span>
                   </div>
                 </div>
 
                 <div className="stat-item blue-stat">
                   <div className="stat-icon"><ListChecks size={24} /></div>
                   <div className="stat-info">
                     <span className="stat-label">완료한 측정 세션 수</span>
                     <span className="stat-value">{selectedData.sessions}회</span>
                   </div>
                 </div>
               </div>
             </div>
          ) : (
            <div className="empty-history-detail">
              <div className="empty-icon-small">🐢💤</div>
              <h4>이 날은 측정 기록이 없어요!</h4>
              <p>바른 자세 측정을 쉬어갔던 날이네요.</p>
              {selectedDateStr === toDateString(new Date().getFullYear(), new Date().getMonth(), new Date().getDate()) && (
                <button className="btn-primary" onClick={() => navigate('/dashboard')} style={{marginTop: '16px'}}>
                  오늘 기록 시작하기
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

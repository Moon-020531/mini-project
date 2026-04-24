import React, { useRef, useEffect, useState, useCallback, forwardRef, useImperativeHandle } from 'react';
import Webcam from 'react-webcam';
import axios from 'axios';

/* global Pose, Camera */

const ESP32_URL = 'http://192.168.0.46';
const BACKEND_URL = 'http://localhost:8080/api/log';

const WebcamDetector = forwardRef(({ username, onDataSaved, onMeasuringChange, onPresenceChange }, ref) => {
  const webcamRef = useRef(null);
  const canvasRef = useRef(null);
  const cameraRef = useRef(null);

  useImperativeHandle(ref, () => ({
    stopMeasurement: () => {
      setIsMeasuring(false);
    }
  }));

  // UI States
  const [isMeasuring, setIsMeasuring] = useState(false);
  const [calibMode, setCalibMode] = useState('NONE');
  const [calibProgress, setCalibProgress] = useState(0);

  // 모든 측정 로직은 Ref 기반으로 관리 (React 렌더링과 분리)
  const calibModeRef = useRef('NONE');
  const isCurrentlyWarningRef = useRef(false);
  const warningCountRef = useRef(0);
  const goodPostureStartRef = useRef(Date.now());
  const totalGoodTimeRef = useRef(0);
  const lastLogTimeRef = useRef(Date.now());
  const dangerStartTimeRef = useRef(null);

  const calibDataQueueRef = useRef([]);
  const baselineRef = useRef({ depthZ: 0, dropY: 0, ratio: 0 });
  const penaltyQueueRef = useRef([]);
  const smoothPenaltyRef = useRef(0);
  const isPresentRef = useRef(true);

  // calibMode state와 ref를 동기화
  useEffect(() => { calibModeRef.current = calibMode; }, [calibMode]);

  const sendESP32Command = useCallback((isWarning) => {
    const endpoint = isWarning ? '/warning' : '/normal';
    fetch(`${ESP32_URL}${endpoint}`, { method: 'GET', mode: 'no-cors' }).catch(() => {});
  }, []);

  const sendBackendLog = useCallback((goodTime, warnCount) => {
    console.log(`[로그 전송] goodTime=${goodTime}, warnCount=${warnCount}, user=${username}`);
    axios.post(BACKEND_URL, {
      goodPostureTime: goodTime,
      warningCount: warnCount,
      username: username
    }).then(() => {
      console.log('[로그 전송 성공]');
      if (onDataSaved) onDataSaved();
    }).catch((err) => {
      console.error('[로그 전송 실패]', err.message);
    });
  }, [username, onDataSaved]);

  const getMetrics = (landmarks, width, height) => {
    const earL = landmarks[7];
    const earR = landmarks[8];
    const nose = landmarks[0];
    const shoulderL = landmarks[11];
    const shoulderR = landmarks[12];

    const shoulderMidY = (shoulderL.y + shoulderR.y) / 2 * height;
    const shoulderMidZ = (shoulderL.z + shoulderR.z) / 2;
    const noseY = nose.y * height;

    const depthZ = shoulderMidZ - nose.z;
    const dropY = shoulderMidY - noseY;

    const faceWidth = Math.abs(earR.x - earL.x) * width;
    const shoulderWidth = Math.abs(shoulderR.x - shoulderL.x) * width;
    const ratio = shoulderWidth === 0 ? 0 : faceWidth / shoulderWidth;

    return { depthZ, dropY, ratio };
  };

  const handleCalibration = () => {
    calibModeRef.current = 'RUNNING';
    setCalibMode('RUNNING');
    calibDataQueueRef.current = [];
    setCalibProgress(0);
  };

  // isMeasuring 상태와 캘리브레이션 'DONE' 상태가 모두 만족될 때 대시보드 타이머를 작동시킴
  useEffect(() => {
    if (onMeasuringChange) {
      const isSessionReallyStarted = isMeasuring && calibMode === 'DONE';
      onMeasuringChange(isSessionReallyStarted);
    }
  }, [isMeasuring, calibMode, onMeasuringChange]);

  const onResults = useCallback((results) => {
    if (!canvasRef.current || !webcamRef.current || !webcamRef.current.video) return;

    const videoWidth = webcamRef.current.video.videoWidth;
    const videoHeight = webcamRef.current.video.videoHeight;
    const canvasCtx = canvasRef.current.getContext('2d');

    canvasRef.current.width = videoWidth;
    canvasRef.current.height = videoHeight;

    canvasCtx.save();
    canvasCtx.clearRect(0, 0, videoWidth, videoHeight);
    canvasCtx.drawImage(results.image, 0, 0, videoWidth, videoHeight);

    let color = '#3b82f6';
    let statusText = '대기 중';
    let penalty = 0;
    const mode = calibModeRef.current;

    if (results.poseLandmarks) {
      if (!isPresentRef.current) {
        isPresentRef.current = true;
        if (onPresenceChange) onPresenceChange(true);
      }
      const nose = results.poseLandmarks[0];
      const sL = results.poseLandmarks[11];
      const sR = results.poseLandmarks[12];

      // 뼈대 렌더링
      canvasCtx.beginPath();
      canvasCtx.moveTo(sL.x * videoWidth, sL.y * videoHeight);
      canvasCtx.lineTo(sR.x * videoWidth, sR.y * videoHeight);
      canvasCtx.lineTo(nose.x * videoWidth, nose.y * videoHeight);
      canvasCtx.closePath();
      canvasCtx.strokeStyle = 'rgba(255,255,255,0.4)';
      canvasCtx.lineWidth = 2;
      canvasCtx.stroke();

      const metrics = getMetrics(results.poseLandmarks, videoWidth, videoHeight);

      // ======= 캘리브레이션 진행 =======
      if (mode === 'RUNNING') {
        calibDataQueueRef.current.push(metrics);
        const prog = Math.min(100, Math.floor((calibDataQueueRef.current.length / 90) * 100));
        setCalibProgress(prog);

        if (calibDataQueueRef.current.length >= 90) {
          const sum = calibDataQueueRef.current.reduce((acc, cur) => ({
            depthZ: acc.depthZ + cur.depthZ,
            dropY: acc.dropY + cur.dropY,
            ratio: acc.ratio + cur.ratio
          }), { depthZ: 0, dropY: 0, ratio: 0 });

          baselineRef.current = {
            depthZ: sum.depthZ / 90,
            dropY: sum.dropY / 90,
            ratio: sum.ratio / 90,
          };
          console.log('[캘리브레이션 완료] 기준점:', baselineRef.current);

          goodPostureStartRef.current = Date.now();
          lastLogTimeRef.current = Date.now();
          totalGoodTimeRef.current = 0;
          warningCountRef.current = 0;

          calibModeRef.current = 'DONE';
          setCalibMode('DONE');
        }

        color = '#fbbf24';
        statusText = '영점 조준 중... 자세를 유지하세요';
      }
      // ======= 실제 측정 =======
      else if (mode === 'DONE') {
        const b = baselineRef.current;

        const dZ = metrics.depthZ - b.depthZ;
        const scoreZ = Math.max(0, dZ * 500);

        const dY = (b.dropY - metrics.dropY) / videoHeight * 100;
        const scoreY = Math.max(0, dY * 10);

        const dRatio = (metrics.ratio - b.ratio) * 100;
        const scoreRatio = Math.max(0, dRatio * 15);

        const rawPenalty = (scoreZ * 0.4) + (scoreY * 0.4) + (scoreRatio * 0.2);
        const clampedPenalty = Math.min(100, Math.max(0, rawPenalty));

        penaltyQueueRef.current.push(clampedPenalty);
        if (penaltyQueueRef.current.length > 15) penaltyQueueRef.current.shift();
        penalty = penaltyQueueRef.current.reduce((a, b) => a + b, 0) / penaltyQueueRef.current.length;
        smoothPenaltyRef.current = penalty;

        if (penalty < 40) {
          color = '#10b981';
          statusText = '바른 자세';
        } else if (penalty < 70) {
          color = '#fbbf24';
          statusText = '자세 주의';
        } else {
          color = '#ef4444';
          statusText = '거북목 감지';
        }

        // 디바운싱
        if (penalty >= 70) {
          if (!isCurrentlyWarningRef.current) {
            if (!dangerStartTimeRef.current) {
              dangerStartTimeRef.current = Date.now();
            } else if (Date.now() - dangerStartTimeRef.current >= 1000) {
              isCurrentlyWarningRef.current = true;
              warningCountRef.current += 1;
              totalGoodTimeRef.current += (Date.now() - goodPostureStartRef.current) / 1000;
              sendESP32Command(true);
            }
          }
        } else {
          dangerStartTimeRef.current = null;
          if (isCurrentlyWarningRef.current) {
            isCurrentlyWarningRef.current = false;
            goodPostureStartRef.current = Date.now();
            sendESP32Command(false);
          }
        }

        // ★ 10초마다 백엔드 로그 전송 (핵심!)
        const now = Date.now();
        if (now - lastLogTimeRef.current >= 10000) {
          if (!isCurrentlyWarningRef.current) {
            totalGoodTimeRef.current += (now - goodPostureStartRef.current) / 1000;
            goodPostureStartRef.current = now;
          }
          const g = Math.floor(totalGoodTimeRef.current);
          const w = warningCountRef.current;
          console.log(`[10초 타이머 도달] goodTime=${g}, warns=${w}`);
          sendBackendLog(g, w);
          totalGoodTimeRef.current = 0;
          warningCountRef.current = 0;
          lastLogTimeRef.current = now;
        }
      }
    } else {
      // 랜드마크가 검출되지 않음 (사용자가 화면 밖으로 이탈, 자리 비움)
      if (isPresentRef.current) {
        isPresentRef.current = false;
        if (onPresenceChange) onPresenceChange(false);
      }
      color = '#64748b';
      statusText = '자리 비움 🈳 (타이머 일시 정지)';
      
      if (calibModeRef.current === 'DONE') {
        // 자리 비움 시 경고 중이었다면 해제
        if (isCurrentlyWarningRef.current) {
          isCurrentlyWarningRef.current = false;
          sendESP32Command(false);
        }
        // 자리를 비운 동안 시간이 누적되지 않도록 시작점을 계속 현재 시간으로 당겨줌
        goodPostureStartRef.current = Date.now();
      }
    }

    // --- 좌우반전 텍스트 렌더링 ---
    canvasCtx.save();
    canvasCtx.scale(-1, 1);

    canvasCtx.font = 'bold 24px Inter';
    canvasCtx.fillStyle = color;
    canvasCtx.shadowColor = 'rgba(0,0,0,0.8)';
    canvasCtx.shadowBlur = 4;

    canvasCtx.fillText(statusText, -videoWidth + 20, 50);

    if (mode === 'DONE') {
      canvasCtx.fillText(`오차 점수: ${penalty.toFixed(0)} 점`, -videoWidth + 20, 90);
    }

    if (isCurrentlyWarningRef.current) {
      canvasCtx.fillStyle = '#ef4444';
      canvasCtx.fillText('⚠️ 거북목 경고 발동!', -videoWidth + 20, 130);
    }
    canvasCtx.restore();
    canvasCtx.restore();
  }, [sendESP32Command, sendBackendLog]);

  useEffect(() => {
    if (isMeasuring && webcamRef.current && webcamRef.current.video) {
      const pose = new Pose({ locateFile: (f) => `https://cdn.jsdelivr.net/npm/@mediapipe/pose/${f}` });
      pose.setOptions({
        modelComplexity: 0,
        smoothLandmarks: true,
        minDetectionConfidence: 0.5,
        minTrackingConfidence: 0.5,
      });
      pose.onResults(onResults);

      const camera = new Camera(webcamRef.current.video, {
        onFrame: async () => { await pose.send({ image: webcamRef.current.video }); },
        width: 640,
        height: 480,
      });
      camera.start();
      cameraRef.current = camera;
    } else {
      if (cameraRef.current) { cameraRef.current.stop(); cameraRef.current = null; }
      calibModeRef.current = 'NONE';
      setCalibMode('NONE');
      calibDataQueueRef.current = [];
      penaltyQueueRef.current = [];
      dangerStartTimeRef.current = null;
    }
    return () => { if (cameraRef.current) cameraRef.current.stop(); };
  }, [isMeasuring, onResults]);

  return (
    <div className="webcam-detector">
      <div className="detector-header">
        <h3>Live Posture Tracker 👁️</h3>
        <button className={`btn-measure ${isMeasuring ? 'active' : ''}`} onClick={() => setIsMeasuring(!isMeasuring)}>
          {isMeasuring ? '측정 종료하기' : '▶ 카메라 켜기'}
        </button>
      </div>

      <div className="video-container" style={{ position: 'relative' }}>
        {isMeasuring ? (
          <>
            <Webcam ref={webcamRef} style={{ display: 'none' }} mirrored={true} />
            <canvas ref={canvasRef} className="detector-canvas" style={{ transform: 'scaleX(-1)' }} />

            {calibMode === 'NONE' && (
              <div style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', background: 'rgba(0,0,0,0.6)' }}>
                <h2 style={{ color: 'white', marginBottom: '20px' }}>바른 자세 측정을 시작합니다</h2>
                <p style={{ color: '#94a3b8', marginBottom: '30px' }}>등을 펴고 바른 자세로 가운데를 주시해주세요.</p>
                <button className="btn-primary" onClick={handleCalibration} style={{ padding: '12px 32px', fontSize: '1.2rem' }}>
                  자세 영점 조준 (3초)
                </button>
              </div>
            )}

            {calibMode === 'RUNNING' && (
              <div style={{ position: 'absolute', bottom: '30px', left: '50%', transform: 'translateX(-50%)', width: '80%', textAlign: 'center' }}>
                <div style={{ color: 'white', fontWeight: 'bold', marginBottom: '8px' }}>기준점 파악 중... ({calibProgress}%)</div>
                <div style={{ background: 'rgba(255,255,255,0.2)', height: '10px', borderRadius: '5px', overflow: 'hidden' }}>
                  <div style={{ background: '#10b981', width: `${calibProgress}%`, height: '100%', transition: 'width 0.1s' }}></div>
                </div>
              </div>
            )}
          </>
        ) : (
          <div className="camera-off-state">
            <span className="icon">📸</span>
            <p>카메라 켜기 버튼을 눌러주세요.</p>
          </div>
        )}
      </div>
    </div>
  );
});

export default WebcamDetector;

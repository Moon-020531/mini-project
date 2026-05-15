import React, { useRef, useEffect, useState, useCallback, forwardRef, useImperativeHandle } from 'react';
import Webcam from 'react-webcam';
import axios from 'axios';

/* global Pose, FaceMesh, Camera */

const ESP32_URL = 'http://localhost:5000';
const BACKEND_URL = 'http://localhost:8080/api/log';

// ==================== EAR 관련 상수 ====================
// Face Mesh 468+10 기준 눈 랜드마크 인덱스
const LEFT_EYE_IDX = [362, 385, 387, 263, 373, 380];
const RIGHT_EYE_IDX = [33, 160, 158, 133, 153, 144];
const EAR_THRESHOLD = 0.20;
const NORMAL_BLINK_RATE = 15;

// 얼굴 너비 기반 거리 추정 상수
const LEFT_TEMPLE_IDX = 234;
const RIGHT_TEMPLE_IDX = 454;
const KNOWN_FACE_WIDTH_CM = 14.0;
const FOCAL_LENGTH_PX = 600.0;
const SAFE_DISTANCE_CM = 40.0;

// 어깨 비대칭 임계값
const SHOULDER_TILT_THRESHOLD = 10.0;

// ==================== 유틸 함수 ====================
function dist(p1, p2) {
  return Math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2);
}

function calculateEAR(landmarks, eyeIndices, w, h) {
  const pts = eyeIndices.map(idx => [landmarks[idx].x * w, landmarks[idx].y * h]);
  const v1 = dist(pts[1], pts[5]);
  const v2 = dist(pts[2], pts[4]);
  const hz = dist(pts[0], pts[3]);
  if (hz === 0) return 0;
  return (v1 + v2) / (2.0 * hz);
}

function estimateDistance(landmarks, w, h) {
  const lt = landmarks[LEFT_TEMPLE_IDX];
  const rt = landmarks[RIGHT_TEMPLE_IDX];
  const faceW = dist([lt.x * w, lt.y * h], [rt.x * w, rt.y * h]);
  if (faceW < 1) return -1;
  return (KNOWN_FACE_WIDTH_CM * FOCAL_LENGTH_PX) / faceW;
}

function calculateShoulderTilt(sL, sR, w, h) {
  const ly = sL.y * h, ry = sR.y * h;
  const lx = sL.x * w, rx = sR.x * w;
  const dy = Math.abs(ly - ry);
  const dx = Math.abs(lx - rx);
  if (dx === 0) return 90;
  return Math.atan2(dy, dx) * (180 / Math.PI);
}

function clamp(v, lo = 0, hi = 100) { return Math.max(lo, Math.min(hi, v)); }

function calculatePostureScore(cvaAngle, shoulderTilt, distanceCm) {
  const sCva = clamp((cvaAngle - 30) / (70 - 30) * 100);
  const sShoulder = clamp((1 - shoulderTilt / 20) * 100);
  const sDistance = distanceCm > 0 ? clamp((distanceCm - 20) / (50 - 20) * 100) : 50;
  const score = 0.50 * sCva + 0.25 * sShoulder + 0.25 * sDistance;
  return { total: Math.round(score * 10) / 10, sCva: Math.round(sCva), sShoulder: Math.round(sShoulder), sDistance: Math.round(sDistance) };
}

// ==================== 컴포넌트 ====================
const WebcamDetector = forwardRef(({ username, onDataSaved, onMeasuringChange, onPresenceChange, onPostureData }, ref) => {
  const webcamRef = useRef(null);
  const canvasRef = useRef(null);
  const cameraRef = useRef(null);

  useImperativeHandle(ref, () => ({
    stopMeasurement: () => { setIsMeasuring(false); }
  }));

  const [isMeasuring, setIsMeasuring] = useState(false);
  const [calibMode, setCalibMode] = useState('NONE');
  const [calibProgress, setCalibProgress] = useState(0);

  // Refs
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

  // Face Mesh refs
  const faceMeshRef = useRef(null);
  const blinkTimestampsRef = useRef([]);
  const latestFaceDataRef = useRef({ ear: 0.25, blinksPerMin: 0, distanceCm: -1, blinkTotal: 0 });
  const frameCountRef = useRef(0);
  const faceMeshBusyRef = useRef(false);
  // 시간 기반 깜빡임 상태머신
  const eyeClosedRef = useRef(false);      // 현재 눈 감김 상태
  const eyeCloseTimeRef = useRef(0);       // 눈 감김 시작 시각(ms)
  const blinkTotalRef = useRef(0);         // 누적 깜빡임 횟수

  useEffect(() => { calibModeRef.current = calibMode; }, [calibMode]);

  const sendESP32Command = useCallback((isWarning) => {
    const endpoint = isWarning ? '/warning' : '/normal';
    fetch(`${ESP32_URL}${endpoint}`, { method: 'GET', mode: 'no-cors' }).catch(() => {});
  }, []);

  const sendBackendLog = useCallback((goodTime, warnCount, score) => {
    axios.post(BACKEND_URL, {
      goodPostureTime: goodTime,
      warningCount: warnCount,
      postureScore: score,
      username: username
    }).then(() => {
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

  useEffect(() => {
    if (onMeasuringChange) {
      const isSessionReallyStarted = isMeasuring && calibMode === 'DONE';
      onMeasuringChange(isSessionReallyStarted);
    }
  }, [isMeasuring, calibMode, onMeasuringChange]);

  // Face Mesh 결과 핸들러 (시간 기반 상태머신 깜빡임 감지)
  const onFaceResults = useCallback((results) => {
    if (!results.multiFaceLandmarks || results.multiFaceLandmarks.length === 0) {
      console.log('[FaceMesh] 얼굴 미감지');
      return;
    }
    
    const video = webcamRef.current?.video;
    if (!video) return;
    const w = video.videoWidth;
    const h = video.videoHeight;
    const flm = results.multiFaceLandmarks[0];

    // EAR 계산 (양쪽 눈 평균)
    const leftEar = calculateEAR(flm, LEFT_EYE_IDX, w, h);
    const rightEar = calculateEAR(flm, RIGHT_EYE_IDX, w, h);
    const avgEar = (leftEar + rightEar) / 2.0;

    const now = Date.now();

    // ========== 시간 기반 상태머신 깜빡임 판정 ==========
    // 원리: OPEN -> CLOSED(시각 기록) -> OPEN(지속 50~500ms이면 1회 깜빡임)
    // 프레임 스킵과 무관하게 시간만으로 판정하므로 정확도가 높음
    if (avgEar < EAR_THRESHOLD) {
      // 눈이 감겨있는 상태
      if (!eyeClosedRef.current) {
        // OPEN -> CLOSED 전환: 감김 시작 시각 기록
        eyeClosedRef.current = true;
        eyeCloseTimeRef.current = now;
      }
    } else {
      // 눈이 떠있는 상태
      if (eyeClosedRef.current) {
        // CLOSED -> OPEN 전환: 깜빡임 판정
        const closeDuration = now - eyeCloseTimeRef.current;
        // 유효한 깜빡임: 50ms(너무 짧은 노이즈 제외) ~ 500ms(의도적 감기 제외)
        if (closeDuration >= 50 && closeDuration <= 500) {
          blinkTimestampsRef.current.push(now);
          blinkTotalRef.current += 1;
          console.log(`[Blink #${blinkTotalRef.current}] EAR=${avgEar.toFixed(3)}, duration=${closeDuration}ms`);
        }
        eyeClosedRef.current = false;
      }
    }

    // 60초 윈도우 밖 타임스탬프 제거
    blinkTimestampsRef.current = blinkTimestampsRef.current.filter(t => now - t <= 60000);
    const blinksPerMin = blinkTimestampsRef.current.length;

    // 거리 추정
    const distanceCm = estimateDistance(flm, w, h);

    latestFaceDataRef.current = { ear: avgEar, blinksPerMin, distanceCm, blinkTotal: blinkTotalRef.current };
  }, []);

  // Pose 결과 핸들러
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

    // Face Mesh에서 가져온 최신 데이터
    const faceData = latestFaceDataRef.current;
    let shoulderTilt = 0;
    let cvaAngle = 70;

    if (results.poseLandmarks) {
      if (!isPresentRef.current) {
        isPresentRef.current = true;
        if (onPresenceChange) onPresenceChange(true);
      }
      const nose = results.poseLandmarks[0];
      const sL = results.poseLandmarks[11];
      const sR = results.poseLandmarks[12];

      // 어깨 비대칭 계산
      shoulderTilt = calculateShoulderTilt(sL, sR, videoWidth, videoHeight);

      // CVA 각도 추정 (귀-어깨 기반)
      const earR = results.poseLandmarks[8];
      const earX = earR.x * videoWidth, earY = earR.y * videoHeight;
      const shoX = sR.x * videoWidth, shoY = sR.y * videoHeight;
      const dx = Math.abs(shoX - earX);
      const dy = shoY - earY;
      cvaAngle = dx === 0 ? 90 : Math.atan2(dy, dx) * (180 / Math.PI);

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

        // 복합 자세 점수 계산
        const postureScore = calculatePostureScore(cvaAngle, shoulderTilt, faceData.distanceCm);
        
        // 부모 컴포넌트에 실시간 데이터 전달
        if (onPostureData) {
          onPostureData({
            postureScore: postureScore.total,
            scoreCva: postureScore.sCva,
            scoreShoulder: postureScore.sShoulder,
            scoreDistance: postureScore.sDistance,
            cvaAngle: Math.round(cvaAngle),
            shoulderTilt: Math.round(shoulderTilt * 10) / 10,
            distanceCm: Math.round(faceData.distanceCm * 10) / 10,
            ear: Math.round(faceData.ear * 100) / 100,
            blinksPerMin: faceData.blinksPerMin,
            isFatigued: faceData.blinksPerMin < NORMAL_BLINK_RATE,
            isShoulderAsymmetry: shoulderTilt > SHOULDER_TILT_THRESHOLD,
            isTooClose: faceData.distanceCm > 0 && faceData.distanceCm < SAFE_DISTANCE_CM,
            penalty: Math.round(penalty),
          });
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

        // 10초마다 백엔드 로그 전송
        const now = Date.now();
        if (now - lastLogTimeRef.current >= 10000) {
          if (!isCurrentlyWarningRef.current) {
            totalGoodTimeRef.current += (now - goodPostureStartRef.current) / 1000;
            goodPostureStartRef.current = now;
          }
          const g = Math.floor(totalGoodTimeRef.current);
          const w = warningCountRef.current;
          sendBackendLog(g, w, postureScore.total);
          totalGoodTimeRef.current = 0;
          warningCountRef.current = 0;
          lastLogTimeRef.current = now;
        }
      }
    } else {
      if (isPresentRef.current) {
        isPresentRef.current = false;
        if (onPresenceChange) onPresenceChange(false);
      }
      color = '#64748b';
      statusText = '자리 비움 (타이머 일시 정지)';
      
      if (calibModeRef.current === 'DONE') {
        if (isCurrentlyWarningRef.current) {
          isCurrentlyWarningRef.current = false;
          sendESP32Command(false);
        }
        goodPostureStartRef.current = Date.now();
      }
    }

    // --- HUD 렌더링 ---
    canvasCtx.save();
    canvasCtx.scale(-1, 1);

    canvasCtx.font = 'bold 22px Inter';
    canvasCtx.fillStyle = color;
    canvasCtx.shadowColor = 'rgba(0,0,0,0.8)';
    canvasCtx.shadowBlur = 4;

    let yOff = 40;
    canvasCtx.fillText(statusText, -videoWidth + 20, yOff);
    yOff += 32;

    if (mode === 'DONE') {
      // 복합 점수
      const ps = calculatePostureScore(cvaAngle, shoulderTilt, faceData.distanceCm);
      const scoreColor = ps.total >= 80 ? '#10b981' : ps.total >= 60 ? '#fbbf24' : '#ef4444';
      canvasCtx.fillStyle = scoreColor;
      canvasCtx.font = 'bold 26px Inter';
      canvasCtx.fillText(`자세 점수: ${ps.total}/100`, -videoWidth + 20, yOff);
      yOff += 30;

      canvasCtx.font = 'bold 18px Inter';

      // CVA
      canvasCtx.fillStyle = cvaAngle < 50 ? '#ef4444' : '#10b981';
      canvasCtx.fillText(`거북목 각도(CVA): ${Math.round(cvaAngle)}도`, -videoWidth + 20, yOff);
      yOff += 24;

      // 어깨 비대칭
      canvasCtx.fillStyle = shoulderTilt > SHOULDER_TILT_THRESHOLD ? '#ef4444' : '#10b981';
      canvasCtx.fillText(`어깨 기울기: ${shoulderTilt.toFixed(1)}도`, -videoWidth + 20, yOff);
      yOff += 24;

      // 거리
      if (faceData.distanceCm > 0) {
        canvasCtx.fillStyle = faceData.distanceCm < SAFE_DISTANCE_CM ? '#ef4444' : '#10b981';
        canvasCtx.fillText(`모니터 거리: ${faceData.distanceCm.toFixed(0)}cm`, -videoWidth + 20, yOff);
        yOff += 24;
      }

      // EAR & 깜빡임 (눈 상태 시각화)
      const eyeState = eyeClosedRef.current ? '● 감김' : '○ 뜸';
      const eyeStateColor = eyeClosedRef.current ? '#ef4444' : '#60a5fa';
      canvasCtx.fillStyle = eyeStateColor;
      canvasCtx.fillText(`${eyeState}  EAR: ${faceData.ear.toFixed(3)}`, -videoWidth + 20, yOff);
      yOff += 24;
      canvasCtx.fillStyle = '#60a5fa';
      canvasCtx.fillText(`깜빡임: 총 ${faceData.blinkTotal || 0}회 / 분당 ${faceData.blinksPerMin}회`, -videoWidth + 20, yOff);
      yOff += 24;

      // 경고 메시지들
      if (isCurrentlyWarningRef.current) {
        canvasCtx.fillStyle = '#ef4444';
        canvasCtx.font = 'bold 20px Inter';
        canvasCtx.fillText('!! 거북목 경고 !!', -videoWidth + 20, yOff);
        yOff += 26;
      }
      if (shoulderTilt > SHOULDER_TILT_THRESHOLD) {
        canvasCtx.fillStyle = '#f97316';
        canvasCtx.fillText('!! 어깨 비대칭 감지 !!', -videoWidth + 20, yOff);
        yOff += 26;
      }
      if (faceData.distanceCm > 0 && faceData.distanceCm < SAFE_DISTANCE_CM) {
        canvasCtx.fillStyle = '#f97316';
        canvasCtx.fillText('!! 모니터 너무 가까움 !!', -videoWidth + 20, yOff);
        yOff += 26;
      }
      if (faceData.blinksPerMin < NORMAL_BLINK_RATE) {
        canvasCtx.fillStyle = '#a78bfa';
        canvasCtx.fillText('!! 눈 피로 주의 !!', -videoWidth + 20, yOff);
      }
    }

    canvasCtx.restore();
    canvasCtx.restore();
  }, [sendESP32Command, sendBackendLog, onPresenceChange, onPostureData]);

  // 카메라 & 모델 초기화
  useEffect(() => {
    let faceMeshInterval = null;
    let faceMeshTimer = null;

    if (isMeasuring && webcamRef.current && webcamRef.current.video) {
      // ===== Pose 모델 (즉시 시작) =====
      const pose = new Pose({ locateFile: (f) => `https://cdn.jsdelivr.net/npm/@mediapipe/pose/${f}` });
      pose.setOptions({
        modelComplexity: 0,
        smoothLandmarks: true,
        minDetectionConfidence: 0.5,
        minTrackingConfidence: 0.5,
      });
      pose.onResults(onResults);

      const camera = new Camera(webcamRef.current.video, {
        onFrame: async () => {
          await pose.send({ image: webcamRef.current.video });
        },
        width: 640,
        height: 480,
      });
      camera.start();
      cameraRef.current = camera;
      console.log('[Pose] 카메라 & Pose 모델 시작됨');

      // ===== Face Mesh (2초 후 지연 시작 — Pose WASM과 충돌 방지) =====
      faceMeshTimer = setTimeout(() => {
        try {
          if (typeof FaceMesh === 'undefined') {
            console.error('[FaceMesh] ❌ FaceMesh 글로벌이 없습니다! index.html에 CDN 스크립트를 확인하세요.');
            return;
          }

          console.log('[FaceMesh] 모델 초기화 시작...');
          const fm = new FaceMesh({ locateFile: (f) => `https://cdn.jsdelivr.net/npm/@mediapipe/face_mesh/${f}` });
          fm.setOptions({
            maxNumFaces: 1,
            refineLandmarks: true,
            minDetectionConfidence: 0.5,
            minTrackingConfidence: 0.5,
          });
          fm.onResults((results) => {
            console.log('[FaceMesh] 결과 수신됨, faces:', results.multiFaceLandmarks?.length || 0);
            onFaceResults(results);
          });
          faceMeshRef.current = fm;
          console.log('[FaceMesh] ✅ 모델 등록 완료, interval 시작');

          // 초당 3회 독립 실행
          faceMeshInterval = setInterval(() => {
            if (faceMeshRef.current && !faceMeshBusyRef.current && webcamRef.current?.video) {
              faceMeshBusyRef.current = true;
              faceMeshRef.current.send({ image: webcamRef.current.video })
                .then(() => {
                  faceMeshBusyRef.current = false;
                })
                .catch((err) => {
                  console.error('[FaceMesh] ❌ send 실패:', err);
                  faceMeshBusyRef.current = false;
                });
            }
          }, 333);

        } catch (err) {
          console.error('[FaceMesh] ❌ 초기화 실패:', err);
        }
      }, 2000);  // Pose가 안정화될 때까지 2초 대기

    } else {
      if (cameraRef.current) { cameraRef.current.stop(); cameraRef.current = null; }
      faceMeshRef.current = null;
      calibModeRef.current = 'NONE';
      setCalibMode('NONE');
      calibDataQueueRef.current = [];
      penaltyQueueRef.current = [];
      dangerStartTimeRef.current = null;
      eyeClosedRef.current = false;
      eyeCloseTimeRef.current = 0;
      blinkTotalRef.current = 0;
      blinkTimestampsRef.current = [];

      if (onPostureData) {
        onPostureData(null);
      }
    }
    return () => {
      if (cameraRef.current) cameraRef.current.stop();
      if (faceMeshInterval) clearInterval(faceMeshInterval);
      if (faceMeshTimer) clearTimeout(faceMeshTimer);
    };
  }, [isMeasuring, onResults, onFaceResults, onPostureData]);

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

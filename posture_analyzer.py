"""
==========================================================================
  PostureAnalyzer v2.0 - 비전 기반 종합 자세 분석 시스템
==========================================================================
  [기능 1] 눈 깜빡임 & 피로도 감지 (MediaPipe Face Mesh + EAR)
  [기능 2] 어깨 비대칭 & 모니터 거리 측정 (Pose + 비례식 Depth)
  [기능 3] 거북목 감지 (CVA, 기존 기능 유지)
  [종합]   복합 자세 점수 (Posture Score, 100점 만점)
==========================================================================
"""

import cv2
import mediapipe as mp
import math
import time
import collections
import requests
import threading

# ======================== 통신 설정 ========================
ESP32_URL = "http://192.168.0.21"
BACKEND_URL = "http://localhost:8080/api/log"


def send_esp32_request(is_warning):
    """ESP32 디바이스에 상태 변경 요청을 비동기 전송합니다."""
    try:
        endpoint = "/warning" if is_warning else "/normal"
        requests.post(ESP32_URL + endpoint, timeout=1.0)
    except requests.exceptions.Timeout:
        pass  # ESP32가 데이터는 수신했으나 응답이 지연된 경우 (정상)
    except requests.exceptions.ConnectionError:
        print(f"[통신 에러] ESP32({ESP32_URL})에 접속할 수 없습니다.")
    except requests.exceptions.RequestException as e:
        print(f"[통신 에러] ESP32 요청 실패: {e}")


def send_backend_log(good_time, warning_count, username):
    """누적 통계 데이터를 백엔드 서버에 JSON POST로 전송합니다."""
    try:
        data = {
            "goodPostureTime": good_time,
            "warningCount": warning_count,
            "username": username,
        }
        requests.post(BACKEND_URL, json=data, timeout=3)
        print(f"[백엔드 로깅 완료] 좋은 자세: {good_time}초 / 경고: {warning_count}회")
    except requests.exceptions.ConnectionError:
        print(f"[알림] 백엔드 서버 미연결 - 로그 스킵 (기록: {good_time}초)")
    except requests.exceptions.RequestException as e:
        print(f"[통신 에러] 백엔드 요청 실패: {e}")


# ======================== MediaPipe 초기화 ========================
mp_pose = mp.solutions.pose
mp_face_mesh = mp.solutions.face_mesh
mp_drawing = mp.solutions.drawing_utils

# Pose 모델 (어깨/귀 랜드마크 -> 거북목 CVA, 어깨 비대칭)
pose = mp_pose.Pose(
    static_image_mode=False,
    model_complexity=0,       # 속도 우선
    smooth_landmarks=True,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5,
)

# Face Mesh 모델 (눈 깜빡임 EAR, 얼굴 너비 기반 거리 추정)
face_mesh = mp_face_mesh.FaceMesh(
    static_image_mode=False,
    max_num_faces=1,
    refine_landmarks=True,    # 눈/홍채 정밀 랜드마크 활성화
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5,
)


# ================================================================
#  [기능 1] 눈 깜빡임 & 피로도 - EAR(Eye Aspect Ratio) 계산
# ================================================================
"""
EAR (Eye Aspect Ratio) 공식 - Soukupova & Cech (2016)

눈의 6개 랜드마크 좌표 P1~P6에 대해:

        EAR = (||P2-P6|| + ||P3-P5||) / (2 * ||P1-P4||)

    P1, P4 : 눈꼬리 양 끝점 (수평 방향)
    P2, P6 : 상안검(위 눈꺼풀) 양측 포인트
    P3, P5 : 하안검(아래 눈꺼풀) 양측 포인트

  - 눈을 크게 뜨면 분자(세로 거리)가 크므로 EAR = 0.25~0.30
  - 눈을 감으면 분자 -> 0이므로 EAR = 0.05 이하
  - 임계값(threshold)은 보통 0.20~0.22로 설정하며,
    연속 3프레임 이상 EAR < threshold이면 1회 깜빡임으로 카운트합니다.
"""

# Face Mesh 468+10 기준 눈 랜드마크 인덱스
# 왼쪽 눈: P1=362, P2=385, P3=387, P4=263, P5=373, P6=380
# 오른쪽 눈: P1=33, P2=160, P3=158, P4=133, P5=153, P6=144
LEFT_EYE_IDX = [362, 385, 387, 263, 373, 380]
RIGHT_EYE_IDX = [33, 160, 158, 133, 153, 144]

EAR_THRESHOLD = 0.18      # EAR이 이 값 미만이면 '눈 감음'으로 판정 (웹캔 환경에서는 0.18이 적합)
EAR_CONSEC_FRAMES = 2     # 연속 N프레임 이상 감겨야 1회 깜빡임 (2프레임으로 완화)
NORMAL_BLINK_RATE = 15    # 정상 분당 깜빡임 횟수 기준
FACE_MESH_SKIP = 4        # Face Mesh는 4프레임마다 1회만 처리 (성능 최적화)


def _dist(p1, p2):
    """두 점 사이의 유클리드 거리를 반환합니다."""
    return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)


def calculate_ear(landmarks, eye_indices, w, h):
    """
    한쪽 눈의 EAR(Eye Aspect Ratio)을 계산합니다.

    Parameters
    ----------
    landmarks : mediapipe face_mesh 랜드마크 리스트
    eye_indices : 눈 랜드마크 6개 인덱스 [P1, P2, P3, P4, P5, P6]
    w, h : 프레임의 너비/높이 (정규화 좌표 -> 픽셀 변환용)

    Returns
    -------
    ear : float - 해당 눈의 EAR 값
    """
    # 6개 점을 픽셀 좌표로 변환
    pts = []
    for idx in eye_indices:
        lm = landmarks[idx]
        pts.append((lm.x * w, lm.y * h))

    # P1=pts[0], P2=pts[1], P3=pts[2], P4=pts[3], P5=pts[4], P6=pts[5]
    # EAR = (||P2-P6|| + ||P3-P5||) / (2 * ||P1-P4||)
    vertical_1 = _dist(pts[1], pts[5])   # ||P2-P6||
    vertical_2 = _dist(pts[2], pts[4])   # ||P3-P5||
    horizontal = _dist(pts[0], pts[3])   # ||P1-P4||

    if horizontal == 0:
        return 0.0

    ear = (vertical_1 + vertical_2) / (2.0 * horizontal)
    return ear


# ================================================================
#  [기능 2-A] 어깨 비대칭 감지
# ================================================================
"""
어깨 비대칭(Shoulder Asymmetry) 판정 원리:

  1) y좌표 차이 (dy):
     양쪽 어깨(Pose 11번/12번)의 y좌표 차이가 크면 한쪽이 들리거나
     처져 있는 비대칭 자세(예: 턱 괴기, 짝다리)입니다.

  2) 기울기 각도 (tilt_angle):
     tilt_angle = atan2(|y_left - y_right|, |x_left - x_right|)
     이 값이 0도에 가까우면 수평(정상), 커질수록 비대칭이 심합니다.
     보통 10도 이상이면 유의미한 비대칭으로 판정합니다.
"""

SHOULDER_TILT_THRESHOLD = 10.0  # 어깨 기울기 경고 기준 (도)


def calculate_shoulder_asymmetry(left_shoulder, right_shoulder, w, h):
    """
    어깨 비대칭 수치(기울기 각도)를 계산합니다.

    Returns
    -------
    tilt_deg : float - 어깨 기울기 각도 (0도=완벽 수평)
    dy_px    : float - 양 어깨 y좌표 차이 (픽셀)
    """
    lx, ly = left_shoulder.x * w, left_shoulder.y * h
    rx, ry = right_shoulder.x * w, right_shoulder.y * h

    dy = abs(ly - ry)
    dx = abs(lx - rx)

    if dx == 0:
        return 90.0, dy

    tilt_rad = math.atan2(dy, dx)
    tilt_deg = math.degrees(tilt_rad)
    return tilt_deg, dy


# ================================================================
#  [기능 2-B] 모니터(카메라) 거리 추정
# ================================================================
"""
얼굴 너비 기반 거리 추정 (핀홀 카메라 모델 비례식):

    Distance = (KNOWN_FACE_WIDTH x Focal_Length) / face_width_in_pixels

  - KNOWN_FACE_WIDTH : 성인 평균 얼굴 너비 = 14.0 cm
  - Focal_Length : 캘리브레이션으로 구하거나, 처음 측정 시 기준 거리(50cm)에서의
                   얼굴 픽셀 너비로 역산합니다.
  - face_width_in_pixels : Face Mesh 랜드마크 중 좌우 관자놀이(454번, 234번)
                           사이의 픽셀 거리로 측정합니다.

  대안적으로 동공 간 거리(IPD = 6.3cm)나 동공 직경(=1.17cm)을
  기준으로 쓸 수도 있지만, 얼굴 너비가 가장 안정적인 추정값을 줍니다.

  * 초점 거리가 카메라마다 다르므로 FOCAL_LENGTH_PX를 실측 보정하면
    정확도가 크게 올라갑니다.
"""

KNOWN_FACE_WIDTH_CM = 14.0   # 성인 평균 얼굴 너비 (cm)
FOCAL_LENGTH_PX = 600.0      # 추정 초점 거리 (픽셀) - 카메라별 보정 권장
SAFE_DISTANCE_CM = 40.0      # 권장 최소 시청 거리 (cm)

# Face Mesh 관자놀이 인덱스 (좌: 234, 우: 454)
LEFT_TEMPLE_IDX = 234
RIGHT_TEMPLE_IDX = 454


def estimate_distance(landmarks, w, h):
    """
    얼굴 너비 기반으로 사용자-카메라 간 거리를 추정합니다.

    [비례식]
        distance_cm = (KNOWN_FACE_WIDTH_CM x FOCAL_LENGTH_PX) / face_width_px

    Returns
    -------
    distance_cm : float - 추정 거리 (cm), 추정 불가 시 -1
    """
    lt = landmarks[LEFT_TEMPLE_IDX]
    rt = landmarks[RIGHT_TEMPLE_IDX]

    face_width_px = _dist((lt.x * w, lt.y * h), (rt.x * w, rt.y * h))

    if face_width_px < 1:
        return -1.0

    distance_cm = (KNOWN_FACE_WIDTH_CM * FOCAL_LENGTH_PX) / face_width_px
    return distance_cm


# ================================================================
#  [기능 3] 거북목 CVA 각도 계산 (기존 로직 유지)
# ================================================================
def calculate_cva(ear_x, ear_y, shoulder_x, shoulder_y):
    """
    귓불(Ear)과 어깨(Shoulder) 좌표로 CVA(Craniovertebral Angle)를 계산합니다.

    [수식]
        dx = |shoulder_x - ear_x|
        dy = shoulder_y - ear_y        (어깨가 귀 아래에 위치)
        CVA = atan2(dy, dx)  ->  degree 변환

    - 50도 미만 -> 거북목 경고
    - 55도 이상 -> 정상 복귀 (히스테리시스)
    """
    dx = abs(shoulder_x - ear_x)
    dy = shoulder_y - ear_y

    if dx == 0:
        return 90.0

    angle_rad = math.atan2(dy, dx)
    return math.degrees(angle_rad)


# ================================================================
#  [종합] 복합 자세 점수 (Posture Score) 산출
# ================================================================
"""
복합 자세 점수 산출 공식 (100점 만점):

  Posture Score = w1 * S_cva + w2 * S_shoulder + w3 * S_distance

  가중치: w1=0.50 (거북목), w2=0.25 (어깨), w3=0.25 (거리)

  각 부분 점수:
    S_cva      = clamp((CVA - 30) / (70 - 30) x 100, 0, 100)
                 -> CVA 30도 이하 = 0점, 70도 이상 = 100점 (선형 보간)

    S_shoulder = clamp((1 - tilt_deg / 20) x 100, 0, 100)
                 -> 기울기 0도 = 100점, 20도 이상 = 0점

    S_distance = clamp((distance - 20) / (50 - 20) x 100, 0, 100)
                 -> 20cm 이하 = 0점, 50cm 이상 = 100점
                 (40cm 이상이면 충분히 높은 점수)
"""

W_CVA = 0.50
W_SHOULDER = 0.25
W_DISTANCE = 0.25


def _clamp(value, lo=0.0, hi=100.0):
    return max(lo, min(hi, value))


def calculate_posture_score(cva_angle, shoulder_tilt, distance_cm):
    """
    거북목 각도, 어깨 비대칭, 모니터 거리를 종합하여
    0~100점의 자세 점수를 산출합니다.
    """
    # 부분 점수 계산 (선형 보간 후 클램프)
    s_cva = _clamp((cva_angle - 30) / (70 - 30) * 100)
    s_shoulder = _clamp((1 - shoulder_tilt / 20) * 100)

    if distance_cm > 0:
        s_distance = _clamp((distance_cm - 20) / (50 - 20) * 100)
    else:
        s_distance = 50.0  # 거리 추정 불가 시 중립 점수

    score = W_CVA * s_cva + W_SHOULDER * s_shoulder + W_DISTANCE * s_distance
    return round(score, 1), round(s_cva, 1), round(s_shoulder, 1), round(s_distance, 1)


# ================================================================
#  유틸: 점수에 따른 컬러 반환
# ================================================================
def score_color(score):
    """점수에 따라 BGR 색상을 반환합니다."""
    if score >= 80:
        return (0, 230, 0)      # 초록 - 우수
    elif score >= 60:
        return (0, 200, 255)    # 주황 - 주의
    else:
        return (0, 0, 255)      # 빨강 - 위험


# ================================================================
#  메인 루프
# ================================================================
def main():
    print("=" * 50)
    print("  PostureAnalyzer v2.0 - 종합 자세 분석 시스템")
    print("=" * 50)

    username = input("유저 아이디를 입력하세요: ").strip() or "anonymous"
    print(f"\n[{username}] 님의 자세 분석을 시작합니다.\n")

    cap = cv2.VideoCapture(0)

    # ---------- 거북목 히스테리시스 상태 ----------
    is_currently_warning = False
    warning_count = 0
    good_posture_start_time = time.time()
    total_good_time = 0
    last_log_time = time.time()
    LOGGING_INTERVAL = 600  # 10분

    # ---------- 눈 깜빡임 상태 ----------
    blink_counter = 0               # 누적 깜빡임 횟수
    ear_below_count = 0             # EAR < threshold 연속 프레임 수
    # 최근 60초간의 깜빡임 타임스탬프를 저장하는 deque
    blink_timestamps = collections.deque()
    BLINK_WINDOW_SEC = 60.0         # 깜빡임 카운팅 윈도우 (초)
    frame_count = 0                  # Face Mesh 프레임 스킵용 카운터

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            print("웹캠 프레임을 가져올 수 없습니다.")
            break

        frame = cv2.flip(frame, 1)  # 거울 모드
        h, w, _ = frame.shape
        image_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # -----------------------------------------------
        #  두 모델을 동일 프레임에 대해 순차 처리
        #  (RGB 변환 1회, writeable 플래그로 복사 최소화)
        # -----------------------------------------------
        image_rgb.flags.writeable = False
        pose_results = pose.process(image_rgb)
        
        # Face Mesh는 FACE_MESH_SKIP 프레임마다 1회만 처리 (성능 최적화)
        frame_count += 1
        if frame_count % FACE_MESH_SKIP == 0:
            face_results = face_mesh.process(image_rgb)
        else:
            face_results = type('obj', (object,), {'multi_face_landmarks': None})()
        image_rgb.flags.writeable = True

        cva_angle = 70.0          # 기본값 (정상)
        shoulder_tilt = 0.0
        distance_cm = -1.0

        # ================== Pose 처리 ==================
        if pose_results.pose_landmarks:
            plm = pose_results.pose_landmarks.landmark

            # 귀/어깨 좌표 추출
            ear_r = plm[mp_pose.PoseLandmark.RIGHT_EAR.value]
            sho_r = plm[mp_pose.PoseLandmark.RIGHT_SHOULDER.value]
            sho_l = plm[mp_pose.PoseLandmark.LEFT_SHOULDER.value]

            ear_px = (int(ear_r.x * w), int(ear_r.y * h))
            sho_r_px = (int(sho_r.x * w), int(sho_r.y * h))

            # [기능 3] CVA 각도
            cva_angle = calculate_cva(ear_px[0], ear_px[1],
                                      sho_r_px[0], sho_r_px[1])

            # [기능 2-A] 어깨 비대칭
            shoulder_tilt, _ = calculate_shoulder_asymmetry(sho_l, sho_r, w, h)

            # ---- 시각화: CVA 라인 ----
            cv2.line(frame, sho_r_px, ear_px, (0, 255, 0), 2)
            cv2.line(frame, (sho_r_px[0] - 50, sho_r_px[1]),
                     (sho_r_px[0] + 50, sho_r_px[1]), (255, 0, 0), 2)
            cv2.circle(frame, ear_px, 5, (0, 0, 255), -1)
            cv2.circle(frame, sho_r_px, 5, (0, 0, 255), -1)

            # ---- 거북목 히스테리시스 판별 & ESP32 통신 ----
            if not is_currently_warning:
                if cva_angle < 50:
                    is_currently_warning = True
                    warning_count += 1
                    total_good_time += (time.time() - good_posture_start_time)
                    threading.Thread(target=send_esp32_request,
                                     args=(True,), daemon=True).start()
            else:
                if cva_angle > 55:
                    is_currently_warning = False
                    good_posture_start_time = time.time()
                    threading.Thread(target=send_esp32_request,
                                     args=(False,), daemon=True).start()

        # ================== Face Mesh 처리 ==================
        avg_ear = 0.25  # 기본값 (눈 뜬 상태)

        if face_results.multi_face_landmarks:
            flm = face_results.multi_face_landmarks[0].landmark

            # [기능 1] EAR 계산 - 양쪽 눈 평균
            left_ear = calculate_ear(flm, LEFT_EYE_IDX, w, h)
            right_ear = calculate_ear(flm, RIGHT_EYE_IDX, w, h)
            avg_ear = (left_ear + right_ear) / 2.0

            # 깜빡임 판정 (연속 프레임 카운팅)
            if avg_ear < EAR_THRESHOLD:
                ear_below_count += 1
            else:
                if ear_below_count >= EAR_CONSEC_FRAMES:
                    blink_counter += 1
                    blink_timestamps.append(time.time())
                ear_below_count = 0

            # 60초 윈도우 밖의 오래된 타임스탬프 제거
            now = time.time()
            while blink_timestamps and (now - blink_timestamps[0]) > BLINK_WINDOW_SEC:
                blink_timestamps.popleft()

            blinks_per_min = len(blink_timestamps)

            # [기능 2-B] 모니터 거리 추정
            distance_cm = estimate_distance(flm, w, h)

        else:
            blinks_per_min = -1  # 얼굴 미감지

        # ================== 복합 자세 점수 ==================
        total_score, s_cva, s_sho, s_dist = calculate_posture_score(
            cva_angle, shoulder_tilt, distance_cm
        )

        # ================== HUD 시각화 ==================
        y_offset = 30
        color_main = score_color(total_score)

        # 종합 점수
        cv2.putText(frame, f"Posture Score: {total_score}/100",
                    (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.9, color_main, 2)
        y_offset += 35

        # CVA 각도
        cva_color = (0, 0, 255) if is_currently_warning else (0, 230, 0)
        cv2.putText(frame, f"CVA Angle: {int(cva_angle)} deg (score:{s_cva})",
                    (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.6, cva_color, 2)
        y_offset += 28

        # 거북목 경고
        if is_currently_warning:
            cv2.putText(frame, "!! WARNING: Turtle Neck !!",
                        (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            y_offset += 28

        # 어깨 비대칭
        sho_color = (0, 0, 255) if shoulder_tilt > SHOULDER_TILT_THRESHOLD else (0, 230, 0)
        cv2.putText(frame, f"Shoulder Tilt: {shoulder_tilt:.1f} deg (score:{s_sho})",
                    (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.6, sho_color, 2)
        y_offset += 28

        if shoulder_tilt > SHOULDER_TILT_THRESHOLD:
            cv2.putText(frame, "!! Shoulder Asymmetry Detected !!",
                        (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
            y_offset += 28

        # 모니터 거리
        if distance_cm > 0:
            dist_color = (0, 0, 255) if distance_cm < SAFE_DISTANCE_CM else (0, 230, 0)
            cv2.putText(frame, f"Distance: {distance_cm:.1f} cm (score:{s_dist})",
                        (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.6, dist_color, 2)
            y_offset += 28

            if distance_cm < SAFE_DISTANCE_CM:
                cv2.putText(frame, f"!! Too Close! (min {SAFE_DISTANCE_CM:.0f}cm) !!",
                            (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                y_offset += 28

        # EAR & 깜빡임
        cv2.putText(frame, f"EAR: {avg_ear:.2f}  Blinks/min: {blinks_per_min}",
                    (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 0), 2)
        y_offset += 28

        # 피로도 경고
        if blinks_per_min != -1 and blinks_per_min < NORMAL_BLINK_RATE:
            cv2.putText(frame,
                        "!! Fatigue Alert: Eye drops & rest recommended !!",
                        (10, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 80, 255), 2)
            y_offset += 28

        # ================== 백엔드 주기 로깅 ==================
        current_time = time.time()
        if current_time - last_log_time >= LOGGING_INTERVAL:
            if not is_currently_warning:
                total_good_time += (current_time - good_posture_start_time)
                good_posture_start_time = current_time

            threading.Thread(
                target=send_backend_log,
                args=(int(total_good_time), warning_count, username),
            ).start()

            total_good_time = 0
            warning_count = 0
            last_log_time = current_time

        # 화면 출력
        cv2.imshow("PostureAnalyzer v2.0", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    # 자원 해제
    cap.release()
    cv2.destroyAllWindows()
    pose.close()
    face_mesh.close()


if __name__ == "__main__":
    main()

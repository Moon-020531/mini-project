import cv2
import mediapipe as mp
import math
import time
import requests
import threading

# 통신을 위한 엔드포인트 URL 설정 (실제 환경에 맞게 IP 및 포트 수정 필요)
ESP32_URL = "http://192.168.0.21" # 예: ESP32 로컬 IP (끝에 슬래시 제외)
BACKEND_URL = "http://localhost:8080/api/log" # 예: Spring Boot 로깅 API

def send_esp32_request(is_warning):
    """ ESP32 디바이스에 상태 변경 요청을 전송합니다. (Frame 병목을 방지하기 위해 비동기 권장) """
    try:
        # POST 요청으로 변경, 엔드포인트를 /warning 또는 /normal로 분리
        endpoint = "/warning" if is_warning else "/normal"
        requests.post(ESP32_URL + endpoint, timeout=1.0)
    except requests.exceptions.Timeout:
        # ESP32가 명령(데이터)은 잘 받았으나 처리(부저 울림 등)로 인해 응답을 제때 못 준 경우입니다. 정상 동작입니다!
        pass 
    except requests.exceptions.ConnectionError:
        print(f"[통신 에러] ESP32({ESP32_URL})에 접속할 수 없습니다. IP를 확인하거나 보드 전원을 켜주세요.")
    except requests.exceptions.RequestException as e:
        print(f"[통신 에러] ESP32 요청 실패: {e}")

def send_backend_log(good_time, warning_count, username):
    """ 누적된 통계 데이터를 백엔드 서버에 JSON POST 방식으로 전송합니다. """
    try:
        data = {
            "goodPostureTime": good_time,
            "warningCount": warning_count,
            "username": username
        }
        requests.post(BACKEND_URL, json=data, timeout=3)
        print(f"[백엔드 로깅 완료] 좋은 자세: {good_time}초 / 경고 횟수: {warning_count}회")
    except requests.exceptions.ConnectionError:
        # 백엔드 서버를 아직 띄우지 않은 경우 흔히 발생하므로 친절한 알림으로 대체
        print(f"[알림] 백엔드 서버({BACKEND_URL})가 켜져있지 않아 통계 로그를 넘겼습니다. (기록: {good_time}초)")
    except requests.exceptions.RequestException as e:
        print(f"[통신 에러] 백엔드 요청 실패: {e}")

# MediaPipe Pose 초기화
mp_pose = mp.solutions.pose
pose = mp_pose.Pose(
    static_image_mode=False,
    model_complexity=0, # 연산 속도를 극대화(반응 속도 향상)하기 위해 1에서 0으로 낮춤
    smooth_landmarks=True,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5
)
mp_drawing = mp.solutions.drawing_utils

def calculate_angle(x1, y1, x2, y2):
    """
    귓불(Ear) 좌표(x1, y1)와 어깨(Shoulder) 좌표(x2, y2)를 이용하여
    수평선(x축)을 기준으로 한 전방머리자세각도(CVA, Craniovertebral Angle)를 계산합니다.
    
    [수식 원리]
    - 이미지 좌표계에서는 상단이 y=0이고, 아래로 갈수록 y값이 커집니다.
    - dx = |x1 - x2| (어깨와 귀의 가로 거리차)
    - dy = |y1 - y2| (어깨와 귀의 세로 거리차)
    - 의학적 CVA 측정 방식은 7번 경추(어깨 선상)를 지나는 수평선 대비, 경추에서 트라구스(귀)를 이은 선이 이루는 각도입니다.
    - 즉, 밑변(dx), 높이(dy)의 직각삼각형에서 "math.atan2(dy, dx)"가 CVA가 됩니다.
    - 각도(theta)가 작을수록(보통 50~53도 미만) 머리가 앞으로 많이 빠졌음(거북목)을 의미합니다.
    """
    
    # 가로, 세로 거리 계산
    dx = abs(x2 - x1) 
    dy = y2 - y1 # y2(어깨)가 y1(귀)보다 밑에 있으므로 주로 y2 > y1
    
    if dx == 0: # dx가 0에 가까우면 머리가 완벽하게 서있는 상태 (90도)
        return 90.0

    # 아크탄젠트를 이용해 수평 기준 라디안을 구하고, degree로 변환 (dy, dx 순서)
    angle_rad = math.atan2(dy, dx)
    angle_deg = math.degrees(angle_rad)
    
    return angle_deg

def main():
    print("=" * 40)
    print("거북목 감지 스마트 데스크 시스템")
    print("=" * 40)
    # 실행 시 사용자 아이디를 입력받습니다.
    username = input("현재 데스크를 사용할 유저 아이디를 입력하세요: ").strip()
    if not username:
        print("아이디가 입력되지 않았습니다. 기본값 'anonymous'로 측정합니다.")
        username = "anonymous"
        
    print(f"\n[{username}] 님의 자세 측정을 시작합니다. (10분마다 웹 서버로 전송됩니다.)\n")

    # 웹캠 연결 (0번 카메라)
    cap = cv2.VideoCapture(0)

    # ========== 통신 및 로깅 상태 관리를 위한 변수 ==========
    is_currently_warning = False # 현재 경고 상태 여부 추적 (도배 방지)
    warning_count = 0            # 단위 시간동안 경고가 발생한 횟수
    
    good_posture_start_time = time.time() # 좋은 자세 유지가 시작된 시점
    total_good_time = 0                   # 백엔드에 보낼 단위 시간 내의 좋은 자세 누적 시간(초)
    
    last_log_time = time.time()  # 마지막으로 백엔드에 로깅한 시점
    LOGGING_INTERVAL = 600       # 백엔드 전송 주기 (예: 600초 = 10분)
    # ========================================================

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            print("웹캠 프레임을 가져올 수 없습니다.")
            break

        # 좌우 반전 (거울 모드 구현)
        frame = cv2.flip(frame, 1)
        
        # MediaPipe 처리를 위해 BGR 영상을 RGB로 변환
        image_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # 포즈 랜드마크 추출
        results = pose.process(image_rgb)

        if results.pose_landmarks:
            landmarks = results.pose_landmarks.landmark
            
            # 해상도를 곱해 실제 영상의 픽셀 좌표를 구합니다.
            h, w, c = frame.shape
            
            # 왼쪽 귀와 왼쪽 어깨 (인덱스: 7, 11) 또는 오른쪽 귀와 오른쪽 어깨 (인덱스: 8, 12)
            # 웹캠 배치나 사용자가 앉은 방향에 따라 좌/우측 중 한 쪽을 선택하거나 평균을 사용할 수 있습니다.
            # 여기서는 편의상 오른쪽(관찰자 기준)을 기준으로 합니다.
            ear = landmarks[mp_pose.PoseLandmark.RIGHT_EAR.value]
            shoulder = landmarks[mp_pose.PoseLandmark.RIGHT_SHOULDER.value]
            
            # 픽셀 정수 좌표로 변환
            ear_x, ear_y = int(ear.x * w), int(ear.y * h)
            shoulder_x, shoulder_y = int(shoulder.x * w), int(shoulder.y * h)

            # 앞서 정의한 함수를 통해 각도 계산
            angle = calculate_angle(ear_x, ear_y, shoulder_x, shoulder_y)
            
            # ======================== 시각화 부분 ========================
            # 1. 어깨에서 귀를 잇는 선 (실제 기울기)
            cv2.line(frame, (shoulder_x, shoulder_y), (ear_x, ear_y), (0, 255, 0), 2)
            # 2. 어깨를 지나는 수평선 기준선 (CVA 측정을 위한 기준축)
            cv2.line(frame, (shoulder_x - 50, shoulder_y), (shoulder_x + 50, shoulder_y), (255, 0, 0), 2)

            
            # 점 찍기
            cv2.circle(frame, (ear_x, ear_y), 5, (0, 0, 255), -1)
            cv2.circle(frame, (shoulder_x, shoulder_y), 5, (0, 0, 255), -1)

            # ======================== 각도 판별 및 통신 처리 ========================
            # **히스테리시스(Hysteresis) 패턴 적용**:
            # 50도라는 하나의 잣대만 쓰면 49도와 51도를 미친듯이 오가며 통신 요청이 '도배'됩니다.
            # 이 때문에 반응 속도도 느려지고 ESP32의 멈춤 현상(요청 실패 에러)이 발생합니다.
            color = (0, 255, 0) # 기본 녹색 (정상 자세)
            
            if not is_currently_warning:
                # 1. 정상 -> 경고로 갈 때는 50도 미만으로 확실히 떨어졌을 때 트립!
                if angle < 50: 
                    is_currently_warning = True
                    warning_count += 1
                    total_good_time += (time.time() - good_posture_start_time)
                    color = (0, 0, 255) # 붉은색 (경고)
                    cv2.putText(frame, "WARNING: Turtle Neck!", (10, 80), cv2.FONT_HERSHEY_SIMPLEX, 1, color, 2)
                    
                    # 도배 없이 1회만 스레드로 안전하게 비동기 전송
                    threading.Thread(target=send_esp32_request, args=(True,), daemon=True).start()
            else:
                # 2. 경고 -> 정상으로 복구될 때는 55도 이상으로 목을 어느정도 확실히 폈을 때만 해제!
                if angle > 55:
                    is_currently_warning = False
                    good_posture_start_time = time.time() # 정상 타이머 재시작
                    color = (0, 255, 0)
                    
                    threading.Thread(target=send_esp32_request, args=(False,), daemon=True).start()
                else:
                    # 55도를 넘지 못했다면 여전히 경고 상태 유지
                    color = (0, 0, 255)
                    cv2.putText(frame, "WARNING: Turtle Neck!", (10, 80), cv2.FONT_HERSHEY_SIMPLEX, 1, color, 2)
            
            # ========== 백엔드로 주기적인 로깅 (API 통신) ==========
            current_time = time.time()
            if current_time - last_log_time >= LOGGING_INTERVAL:
                # 만약 로깅 시점에 사용자가 '정상' 상태라면, 지금까지 유지 중이던 시간을 더해줍니다.
                if not is_currently_warning:
                    total_good_time += (current_time - good_posture_start_time)
                    good_posture_start_time = current_time # 누적 후 측정 시점 다시 갱신
                
                # 백엔드로 통계데이터 HTTP POST 전송 (마찬가지로 비동기 스레드 활용)
                threading.Thread(target=send_backend_log, args=(int(total_good_time), warning_count, username)).start()
                
                # 전송을 마쳤으니 로깅 변수를 초기화하여 다음 10분 주기를 잴 준비를 합니다.
                total_good_time = 0
                warning_count = 0
                last_log_time = current_time
            # ========================================================

            cv2.putText(frame, f"Angle: {int(angle)} deg", (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, color, 2)

            # (선택 사항) 전체 랜드마크 스켈레톤 그리기
            # mp_drawing.draw_landmarks(frame, results.pose_landmarks, mp_pose.POSE_CONNECTIONS)

        # 화면 출력
        cv2.imshow('Turtle Neck Detector', frame)

        # 'q' 키를 누르면 종료
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    # 자원 해제
    cap.release()
    cv2.destroyAllWindows()

if __name__ == '__main__':
    main()

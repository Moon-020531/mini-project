"""
==========================================================================
  TurtleNeckDetectorV2 - ROS 2 기반 거북목 감지 & 토픽 발행 노드
==========================================================================
  [핵심 특징]
    1. 개인별 기준점 캘리브레이션 (시작 시 5초간 바른 자세 CVA 측정)
    2. ROS 2 토픽 'posture_status' 발행 (ESP32가 Micro-ROS로 구독)
    3. 3D CVA 계산 + 2D 폴백 (MediaPipe Tasks API 사용)
    4. 좌/우 visibility 기반 자동 선택
    5. 유저 아이디 입력 + 백엔드 10분 주기 로깅

  [판별 로직]
    - 캘리브레이션으로 개인 baseline CVA를 측정합니다.
    - baseline 대비 DROP_THRESHOLD(15도) 이상 떨어지면 거북목 경고.
    - 히스테리시스: 경고 진입과 복귀 사이에 HYSTERESIS_GAP(5도) 여유.
    - 절대 하한 MIN_WARNING_ANGLE(40도) 안전장치 적용.

  [토픽]
    이름 : posture_status
    타입 : std_msgs/msg/Int32
    값   : 0 = 정상 자세 (Good Posture)
           1 = 거북목   (Forward Head Posture / Turtle Neck)

  [실행 환경]
    - OS      : WSL2 / Ubuntu 24.04
    - ROS 2   : Jazzy Jalisco
    - 통신     : Micro-ROS Agent (Docker) → ESP32 직렬 통신

  [실행 방법]
    $ source /opt/ros/jazzy/setup.bash
    $ python3 turtle_neck_detector_v2.py
==========================================================================
"""

import urllib.request
import os
import sys
import math
import signal
import threading
import time
import requests

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

import rclpy
from rclpy.node import Node
from std_msgs.msg import Int32


# ======================== 상수 정의 ========================

# --- 캘리브레이션 설정 ---
CALIBRATION_DURATION = 5.0    # 캘리브레이션 측정 시간 (초)
DROP_THRESHOLD = 15.0         # baseline 대비 이 각도 이상 떨어지면 경고
HYSTERESIS_GAP = 5.0          # 경고 복귀 시 추가 여유 각도
MIN_WARNING_ANGLE = 40.0      # 절대 하한 (이 미만이면 항상 경고)
DEFAULT_BASELINE = 65.0       # 캘리브레이션 실패 시 폴백 기본값

# --- ROS 2 토픽 발행 ---
PUBLISH_INTERVAL_SEC = 0.5    # 0.5초마다 토픽 발행 (2 Hz)

# --- 통신 설정 (웹사이트 연동용) ---
BACKEND_URL = "http://localhost:8080/api/log"
LOGGING_INTERVAL_SEC = 600    # 10분마다 웹 서버로 전송

# --- 웹캠 설정 ---
CAMERA_INDEX = 0              # 웹캠 장치 인덱스

# --- MediaPipe Pose 설정 ---
MIN_DETECTION_CONF = 0.5      # 최소 감지 신뢰도
MIN_TRACKING_CONF  = 0.5      # 최소 추적 신뢰도


# ======================== 백엔드 로깅 함수 ========================

def send_backend_log(good_time, warning_count, username):
    """누적된 통계 데이터를 백엔드 서버에 JSON POST 방식으로 전송합니다."""
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


# ======================== CVA 계산 함수 ========================

def calculate_cva_3d(
    nose_3d:     tuple[float, float, float],
    ear_3d:      tuple[float, float, float],
    shoulder_3d: tuple[float, float, float],
) -> float:
    """
    3D 좌표를 이용한 Craniovertebral Angle (CVA) 계산.

    [의학적 배경]
    CVA는 제7경추(C7)를 지나는 수평선과, C7에서 이주(Tragus, 귓구멍 앞 돌기)를
    잇는 직선이 이루는 각도입니다. MediaPipe에서는 C7을 직접 제공하지 않으므로,
    어깨(Shoulder) 랜드마크를 C7의 근사 좌표로 사용합니다.

    [3D 계산 방법]
    1) 어깨 → 귀 벡터 (neck_vector) 를 구합니다.
    2) 수평 기준 벡터 (horizontal_vector) 를 정의합니다.
    3) 두 벡터 사이의 각도를 구합니다:
           cos(θ) = (A · B) / (|A| × |B|)
    4) nose 좌표는 직접 각도 계산에 사용되지 않지만,
       사용자의 정면/측면 방향 판단 및 가시성(visibility) 보조에 활용됩니다.

    Parameters
    ----------
    nose_3d     : (x, y, z) - 코 끝 3D 좌표 (향후 방향 판단용)
    ear_3d      : (x, y, z) - 귀(Tragus 근사) 3D 좌표
    shoulder_3d : (x, y, z) - 어깨(C7 근사) 3D 좌표

    Returns
    -------
    cva_angle : float - CVA 각도 (도, degree). 값이 작을수록 거북목.
    """

    # 어깨(C7 근사) → 귀 방향 벡터 (3D)
    neck_vec = np.array([
        ear_3d[0] - shoulder_3d[0],  # Δx
        ear_3d[1] - shoulder_3d[1],  # Δy (이미지 좌표계: 아래가 +)
        ear_3d[2] - shoulder_3d[2],  # Δz (깊이 방향)
    ], dtype=np.float64)

    # 수평 기준 벡터: X축 방향 (좌/우 방향)
    horizontal_vec = np.array([1.0, 0.0, 0.0], dtype=np.float64)

    # 벡터 크기(norm)
    neck_mag = np.linalg.norm(neck_vec)
    horiz_mag = np.linalg.norm(horizontal_vec)

    # 벡터 크기가 0에 가까우면 각도 계산이 불가능 → 정상(90도)으로 반환
    if neck_mag < 1e-6 or horiz_mag < 1e-6:
        return 90.0

    # 내적(dot product)을 통한 코사인 값 계산
    cos_theta = np.dot(neck_vec, horizontal_vec) / (neck_mag * horiz_mag)

    # 부동소수점 오차로 cos 값이 [-1, 1] 범위를 벗어나는 경우 클램핑
    cos_theta = np.clip(cos_theta, -1.0, 1.0)

    # 라디안 → 도(degree) 변환
    cva_angle = math.degrees(math.acos(abs(cos_theta)))

    # CVA는 수평으로부터의 양(+)의 각도여야 하므로,
    # 귀가 어깨보다 위에 있으면(= 정상적 자세) 각도를 양수로 보장합니다.
    if ear_3d[1] < shoulder_3d[1]:
        return cva_angle
    else:
        return -cva_angle


def calculate_cva_2d(
    ear_x: float, ear_y: float,
    shoulder_x: float, shoulder_y: float,
) -> float:
    """
    2D 좌표 기반의 단순 CVA 계산 (3D 계산 실패 시 폴백용).

    [수식]
        dx = |shoulder_x - ear_x|
        dy = shoulder_y - ear_y   (어깨가 귀 아래에 위치하므로 보통 양수)
        CVA = atan2(dy, dx) → degree

    - 각도가 작을수록 머리가 앞으로 빠져 있음(거북목)
    - 90도에 가까울수록 머리가 어깨 위에 바르게 위치
    """
    dx = abs(shoulder_x - ear_x)
    dy = shoulder_y - ear_y  # 어깨가 아래에 있으므로 보통 양수

    if dx < 1e-6:
        return 90.0

    angle_rad = math.atan2(dy, dx)
    return math.degrees(angle_rad)


# ======================== 랜드마크 추출 유틸리티 ========================

def get_landmark_3d(
    landmark, frame_w: int, frame_h: int
) -> tuple[float, float, float]:
    """
    MediaPipe 랜드마크에서 3D 픽셀 좌표를 추출합니다.

    - x, y: 프레임 해상도를 곱해 픽셀 좌표로 변환
    - z: 엉덩이(hip) 중심 기준의 상대적 깊이. 프레임 너비를 곱해 대략적 스케일 맞춤.
    """
    px_x = landmark.x * frame_w
    px_y = landmark.y * frame_h
    scaled_z = landmark.z * frame_w
    return (px_x, px_y, scaled_z)


def get_best_side_landmarks(
    landmarks, frame_w: int, frame_h: int
) -> dict:
    """
    좌/우 귀-어깨 랜드마크 중 visibility가 더 높은 쪽을 선택합니다.

    웹캠 앞에서 사용자가 약간 옆을 돌아보면 한쪽 귀/어깨가 가려질 수 있습니다.
    이때 visibility가 더 높은 쪽의 랜드마크를 사용하면 CVA 계산 정확도가 향상됩니다.

    Returns
    -------
    dict with keys:
        'ear_3d', 'shoulder_3d', 'nose_3d' : 3D 좌표 튜플
        'ear_2d', 'shoulder_2d'            : 2D 픽셀 좌표 (정수)
        'side'                             : 'LEFT' 또는 'RIGHT'
        'visibility'                       : 선택된 쪽의 최소 visibility
    또는 None (모든 랜드마크의 visibility가 낮은 경우)
    """
    # Tasks API에서는 인덱스를 직접 사용합니다.
    # 0: nose, 7: left_ear, 8: right_ear, 11: left_shoulder, 12: right_shoulder
    LEFT_EAR  = 7
    LEFT_SHO  = 11
    RIGHT_EAR = 8
    RIGHT_SHO = 12
    NOSE      = 0

    # 좌/우 각각의 최소 visibility 계산
    left_vis  = min(landmarks[LEFT_EAR].visibility, landmarks[LEFT_SHO].visibility)
    right_vis = min(landmarks[RIGHT_EAR].visibility, landmarks[RIGHT_SHO].visibility)

    # 최소 visibility 임계값 (이 이하면 사용 불가로 판단)
    MIN_VISIBILITY = 0.3

    # visibility가 더 높은 쪽 선택 (동률이면 오른쪽 우선)
    if right_vis >= left_vis:
        ear_lm  = landmarks[RIGHT_EAR]
        sho_lm  = landmarks[RIGHT_SHO]
        side    = "RIGHT"
        vis     = right_vis
    else:
        ear_lm  = landmarks[LEFT_EAR]
        sho_lm  = landmarks[LEFT_SHO]
        side    = "LEFT"
        vis     = left_vis

    # visibility가 너무 낮으면 None 반환 → 호출부에서 "감지 불가" 처리
    if vis < MIN_VISIBILITY:
        return None

    nose_lm = landmarks[NOSE]

    return {
        "ear_3d":      get_landmark_3d(ear_lm, frame_w, frame_h),
        "shoulder_3d": get_landmark_3d(sho_lm, frame_w, frame_h),
        "nose_3d":     get_landmark_3d(nose_lm, frame_w, frame_h),
        "ear_2d":      (int(ear_lm.x * frame_w), int(ear_lm.y * frame_h)),
        "shoulder_2d": (int(sho_lm.x * frame_w), int(sho_lm.y * frame_h)),
        "side":        side,
        "visibility":  vis,
    }


# ======================== ROS 2 노드 클래스 ========================

class TurtleNeckDetectorV2(Node):
    """
    거북목 감지 결과를 ROS 2 토픽으로 발행하는 노드 (V2).

    [V2 주요 특징]
    1. 개인별 기준점 캘리브레이션 (시작 시 5초)
    2. 캘리브레이션 기반 동적 임계값 (baseline - DROP_THRESHOLD)
    3. 3D CVA + 2D 폴백
    4. 좌/우 visibility 자동 선택

    [토픽]
        이름   : posture_status
        타입   : std_msgs/msg/Int32
        값     : 0 = 정상, 1 = 거북목
    """

    def __init__(self):
        super().__init__("turtle_neck_detector_v2")

        # ---- 1. 유저 아이디 입력 (backup 기능 유지) ----
        print("=" * 50)
        print("  거북목 감지 스마트 데스크 시스템 V2")
        print("  (ROS 2 + 개인 캘리브레이션)")
        print("=" * 50)
        self.username = input("현재 데스크를 사용할 유저 아이디를 입력하세요: ").strip()
        if not self.username:
            print("아이디가 입력되지 않았습니다. 기본값 'anonymous'로 측정합니다.")
            self.username = "anonymous"

        # ---- 2. 웹캠 초기화 ----
        self.cap = self._init_camera()

        # ---- 3. MediaPipe Tasks API 초기화 ----
        self.model_path = 'pose_landmarker_lite.task'
        self._download_model_if_needed()

        base_options = python.BaseOptions(model_asset_path=self.model_path)
        options = vision.PoseLandmarkerOptions(
            base_options=base_options,
            output_segmentation_masks=False,
            min_pose_detection_confidence=MIN_DETECTION_CONF,
            min_pose_presence_confidence=MIN_TRACKING_CONF,
            min_tracking_confidence=MIN_TRACKING_CONF
        )
        self.detector = vision.PoseLandmarker.create_from_options(options)

        # ---- 4. ★ 캘리브레이션 실행 ----
        self.baseline_angle = self._calibrate()
        self.warning_entry = max(self.baseline_angle - DROP_THRESHOLD, MIN_WARNING_ANGLE)
        self.warning_exit = self.warning_entry + HYSTERESIS_GAP

        self.get_logger().info(f"캘리브레이션 결과:")
        self.get_logger().info(f"  Baseline CVA     = {self.baseline_angle:.1f}°")
        self.get_logger().info(f"  경고 진입 (Entry) = {self.warning_entry:.1f}° 미만")
        self.get_logger().info(f"  정상 복귀 (Exit)  = {self.warning_exit:.1f}° 초과")

        # ---- 5. 히스테리시스 상태 ----
        self.is_warning = False

        # ---- 6. 웹사이트 로깅 변수 (backup 기능 유지) ----
        self.total_good_time = 0
        self.warning_count = 0
        self.good_posture_start_time = time.time()
        self.last_log_time = time.time()

        # ---- 7. 연속 감지 실패 카운터 ----
        self.no_detection_count = 0
        self.NO_DETECTION_LIMIT = 30  # 30프레임 (약 1.5초) 감지 불가 시 경고

        # ---- 8. ROS 2 Publisher 생성 ----
        self.publisher_ = self.create_publisher(Int32, "posture_status", 10)

        # ---- 9. 주기 타이머 생성 (ROS 2 발행용) ----
        self.timer = self.create_timer(PUBLISH_INTERVAL_SEC, self._publish_status)

        # ---- 10. 쓰레드 제어 ----
        self.is_running = True
        self.current_cva_angle = self.baseline_angle
        self.current_side_data = None

        print(f"\n[{self.username}] 님의 자세 측정을 시작합니다. "
              f"(10분마다 웹 서버로 전송됩니다.)\n")

        self.get_logger().info("=" * 55)
        self.get_logger().info("  TurtleNeckDetectorV2 노드가 시작되었습니다.")
        self.get_logger().info(f"  토픽: 'posture_status' (Int32, 0=정상, 1=거북목)")
        self.get_logger().info(f"  웹사이트 로깅: {LOGGING_INTERVAL_SEC}초 주기")
        self.get_logger().info("=" * 55)

        # 카메라 루프를 독립된 쓰레드로 실행 (프레임 저하 방지)
        self.camera_thread = threading.Thread(target=self.run_camera_loop, daemon=True)
        self.camera_thread.start()

    # ======================== 캘리브레이션 ========================

    def _calibrate(self) -> float:
        """
        5초간 바른 자세의 CVA를 연속 측정하여 개인 기준 각도(baseline)를 설정합니다.

        [동작]
        1. 사용자에게 바른 자세로 앉으라고 안내합니다.
        2. CALIBRATION_DURATION(5초)동안 매 프레임마다 CVA를 계산합니다.
        3. 유효한 각도들의 평균을 baseline으로 반환합니다.
        4. 측정 데이터가 부족하면 DEFAULT_BASELINE(65도)을 폴백으로 사용합니다.

        Returns
        -------
        baseline : float - 개인 기준 CVA 각도 (도, degree)
        """
        print("\n" + "=" * 50)
        print("  [캘리브레이션] 바른 자세로 앉아주세요!")
        print("  5초간 기준 각도를 측정합니다...")
        print("=" * 50)

        angles = []
        start_time = time.time()

        while time.time() - start_time < CALIBRATION_DURATION:
            ret, frame = self.cap.read()
            if not ret:
                continue

            frame = cv2.flip(frame, 1)
            h, w, _ = frame.shape

            # MediaPipe로 CVA 측정
            image_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)
            result = self.detector.detect(mp_image)

            cva_text = "Measuring..."

            if result.pose_landmarks:
                landmarks = result.pose_landmarks[0]
                side_data = get_best_side_landmarks(landmarks, w, h)

                if side_data is not None:
                    angle = calculate_cva_3d(
                        nose_3d=side_data["nose_3d"],
                        ear_3d=side_data["ear_3d"],
                        shoulder_3d=side_data["shoulder_3d"],
                    )

                    # 이상값 필터링: 3D 결과가 비정상이면 2D 폴백
                    if angle < -10 or angle > 120:
                        ear_2d = side_data["ear_2d"]
                        sho_2d = side_data["shoulder_2d"]
                        angle = calculate_cva_2d(
                            ear_2d[0], ear_2d[1],
                            sho_2d[0], sho_2d[1],
                        )

                    # 유효 범위 내 각도만 수집
                    if 20 < angle < 100:
                        angles.append(angle)
                        cva_text = f"CVA: {angle:.1f} deg"

                    # 귀-어깨 시각화
                    ear_2d = side_data["ear_2d"]
                    sho_2d = side_data["shoulder_2d"]
                    cv2.line(frame, sho_2d, ear_2d, (0, 255, 255), 2)
                    cv2.circle(frame, ear_2d, 6, (0, 255, 255), -1)
                    cv2.circle(frame, sho_2d, 6, (0, 255, 255), -1)

            # 화면에 카운트다운 및 안내 표시
            remaining = CALIBRATION_DURATION - (time.time() - start_time)
            cv2.putText(frame, f"CALIBRATING... {remaining:.1f}s",
                        (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)
            cv2.putText(frame, "Please sit with GOOD POSTURE",
                        (10, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            cv2.putText(frame, cva_text,
                        (10, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)
            cv2.putText(frame, f"Samples: {len(angles)}",
                        (10, 155), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)

            cv2.imshow("TurtleNeck Detector V2", frame)
            cv2.waitKey(1)

        # 결과 판정
        if len(angles) < 10:
            print(f"[경고] 측정 데이터가 부족합니다 ({len(angles)}개). "
                  f"기본 임계값({DEFAULT_BASELINE}도)을 사용합니다.")
            return DEFAULT_BASELINE

        baseline = sum(angles) / len(angles)
        print(f"\n✅ 캘리브레이션 완료! 기준 CVA 각도: {baseline:.1f}도")
        print(f"   (측정 횟수: {len(angles)}회)")
        print(f"   경고 진입 기준: {max(baseline - DROP_THRESHOLD, MIN_WARNING_ANGLE):.1f}도 미만\n")
        return baseline

    # ======================== 카메라 초기화 ========================

    def _init_camera(self) -> cv2.VideoCapture:
        """
        웹캠을 초기화하고 VideoCapture 객체를 반환합니다.
        Windows 환경과 WSL2 환경 모두 지원합니다.
        """
        self.get_logger().info(f"웹캠(index={CAMERA_INDEX})을 여는 중...")

        # 먼저 기본 백엔드로 시도
        cap = cv2.VideoCapture(CAMERA_INDEX)

        # 기본 백엔드 실패 시 V4L2 백엔드로 시도 (WSL2 환경)
        if not cap.isOpened():
            self.get_logger().info("기본 백엔드 실패, V4L2 백엔드로 재시도합니다...")
            cap = cv2.VideoCapture(CAMERA_INDEX, cv2.CAP_V4L2)

        if not cap.isOpened():
            self.get_logger().error(
                "============================================\n"
                "  웹캠을 열 수 없습니다!\n"
                "  [확인 사항]\n"
                "  1. 카메라가 연결되어 있는지 확인\n"
                "  2. 다른 프로그램에서 카메라를 사용 중인지 확인\n"
                "  3. WSL2: usbipd로 USB 카메라가 연결되었는지 확인\n"
                "============================================"
            )
            raise RuntimeError("웹캠 초기화 실패 - 카메라를 열 수 없습니다.")

        # WSL2 호환을 위한 설정
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        self.get_logger().info(
            f"웹캠이 성공적으로 열렸습니다. "
            f"해상도: {int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}"
            f"x{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}"
        )
        return cap

    def _download_model_if_needed(self):
        """MediaPipe 모델 파일이 없으면 자동으로 다운로드합니다."""
        if not os.path.exists(self.model_path):
            self.get_logger().info(f"모델 파일이 없습니다. {self.model_path} 다운로드를 시작합니다...")
            url = ("https://storage.googleapis.com/mediapipe-models/"
                   "pose_landmarker/pose_landmarker_lite/float16/latest/"
                   "pose_landmarker_lite.task")
            urllib.request.urlretrieve(url, self.model_path)
            self.get_logger().info("모델 다운로드 완료!")

    # ======================== 카메라 루프 (별도 쓰레드) ========================

    def run_camera_loop(self):
        """
        카메라 프레임을 실시간으로 읽고 MediaPipe 연산을 수행하는 쓰레드.
        타이머에 묶이지 않으므로 프레임 버퍼가 밀리는 현상(렉)이 발생하지 않습니다.
        """
        while self.is_running and self.cap.isOpened() and rclpy.ok():
            # ---- 1. 프레임 읽기 ----
            ret, frame = self.cap.read()
            if not ret:
                continue

            # 좌우 반전 (거울 모드) - 사용자 직관에 맞게
            frame = cv2.flip(frame, 1)
            h, w, _ = frame.shape

            # ---- 2. MediaPipe Tasks 처리 ----
            image_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)
            detection_result = self.detector.detect(mp_image)

            cva_angle = None
            side_data = None

            # ---- 3. 랜드마크 추출 및 CVA 계산 ----
            if detection_result.pose_landmarks:
                self.no_detection_count = 0
                landmarks = detection_result.pose_landmarks[0]
                side_data = get_best_side_landmarks(landmarks, w, h)

                if side_data is not None:
                    cva_angle = calculate_cva_3d(
                        nose_3d=side_data["nose_3d"],
                        ear_3d=side_data["ear_3d"],
                        shoulder_3d=side_data["shoulder_3d"],
                    )

                    # 이상값이면 2D 폴백
                    if cva_angle < -10 or cva_angle > 120:
                        ear_2d = side_data["ear_2d"]
                        sho_2d = side_data["shoulder_2d"]
                        cva_angle = calculate_cva_2d(
                            ear_2d[0], ear_2d[1],
                            sho_2d[0], sho_2d[1],
                        )

                    # ---- 4. 히스테리시스 판별 및 통계 업데이트 ----
                    self._update_posture_status(cva_angle)
            else:
                self.no_detection_count += 1
                if self.no_detection_count == self.NO_DETECTION_LIMIT:
                    self.get_logger().warn("사용자가 감지되지 않습니다.")

            # 상태 저장 (시각화 및 로깅용)
            self.current_cva_angle = cva_angle
            self.current_side_data = side_data

            # ---- 5. 백엔드 로깅 처리 ----
            self._handle_backend_logging()

            # ---- 6. 시각화 (OpenCV 윈도우) ----
            self._visualize(frame, cva_angle, side_data)

            # ---- 7. OpenCV 키 입력 처리 ----
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                self.get_logger().info("'q' 키 입력으로 종료합니다.")
                self.is_running = False
                os.kill(os.getpid(), signal.SIGINT)
                break

    # ======================== 히스테리시스 판별 ========================

    def _update_posture_status(self, cva_angle: float):
        """
        히스테리시스(Hysteresis) 로직으로 거북목 상태를 갱신합니다.
        캘리브레이션에서 측정한 baseline 기반의 동적 임계값을 사용합니다.

        [판별 기준]
        - 정상 → 거북목: CVA < warning_entry (= baseline - DROP_THRESHOLD)
        - 거북목 → 정상: CVA > warning_exit  (= warning_entry + HYSTERESIS_GAP)
        """
        prev_status = self.is_warning

        if not self.is_warning:
            # 정상 → 거북목 전환 조건
            if cva_angle < self.warning_entry:
                self.is_warning = True
        else:
            # 거북목 → 정상 복귀 조건
            if cva_angle > self.warning_exit:
                self.is_warning = False

        # 상태 변경 시 로그 출력 및 통계 업데이트
        if prev_status != self.is_warning:
            if self.is_warning:
                self.get_logger().warn(
                    f"⚠️  거북목 감지! CVA={cva_angle:.1f}° "
                    f"(기준: {self.warning_entry:.1f}° 미만)")
                self.warning_count += 1
                self.total_good_time += (time.time() - self.good_posture_start_time)
            else:
                self.get_logger().info(
                    f"✅ 자세 정상 복귀. CVA={cva_angle:.1f}° "
                    f"(기준: {self.warning_exit:.1f}° 초과)")
                self.good_posture_start_time = time.time()

    # ======================== 백엔드 로깅 ========================

    def _handle_backend_logging(self):
        """10분 주기로 웹사이트용 백엔드 서버에 통계 기록을 전송합니다."""
        current_time = time.time()
        if current_time - self.last_log_time >= LOGGING_INTERVAL_SEC:
            if not self.is_warning:
                self.total_good_time += (current_time - self.good_posture_start_time)
                self.good_posture_start_time = current_time

            # 비동기 전송 (backup 방식 유지)
            threading.Thread(
                target=send_backend_log,
                args=(int(self.total_good_time), self.warning_count, self.username),
                daemon=True
            ).start()

            # 전송 후 초기화
            self.total_good_time = 0
            self.warning_count = 0
            self.last_log_time = current_time

    # ======================== ROS 2 토픽 발행 ========================

    def _publish_status(self):
        """
        현재 거북목 판별 결과를 'posture_status' 토픽으로 발행합니다.

        메시지 값:
            0 → 정상 자세 (Good Posture)
            1 → 거북목   (Forward Head Posture)
        """
        msg = Int32()
        msg.data = 1 if self.is_warning else 0
        self.publisher_.publish(msg)

        self.get_logger().debug(
            f"토픽 발행: posture_status = {msg.data} "
            f"({'거북목' if self.is_warning else '정상'})"
        )

    # ======================== 시각화 ========================

    def _visualize(
        self,
        frame,
        cva_angle: float | None,
        side_data: dict | None,
    ):
        """
        OpenCV 윈도우에 CVA 각도, 랜드마크, 판별 결과, 캘리브레이션 정보를 시각화합니다.
        """
        h_frame = frame.shape[0]
        y_offset = 30

        if cva_angle is not None and side_data is not None:
            ear_2d = side_data["ear_2d"]
            sho_2d = side_data["shoulder_2d"]
            side   = side_data["side"]

            # --- 귀-어깨 연결선 (CVA 시각화) ---
            line_color = (0, 0, 255) if self.is_warning else (0, 255, 0)
            cv2.line(frame, sho_2d, ear_2d, line_color, 2)

            # --- 어깨를 지나는 수평 기준선 ---
            cv2.line(
                frame,
                (sho_2d[0] - 60, sho_2d[1]),
                (sho_2d[0] + 60, sho_2d[1]),
                (255, 180, 0), 2,
            )

            # --- 랜드마크 점 ---
            cv2.circle(frame, ear_2d, 6, (0, 100, 255), -1)   # 귀: 주황
            cv2.circle(frame, sho_2d, 6, (255, 100, 0), -1)   # 어깨: 파랑

            # --- CVA 각도 텍스트 ---
            angle_color = (0, 0, 255) if self.is_warning else (0, 230, 0)
            cv2.putText(
                frame,
                f"CVA: {cva_angle:.1f} deg ({side})",
                (10, y_offset),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, angle_color, 2,
            )
            y_offset += 35

            # --- 판별 결과 ---
            if self.is_warning:
                cv2.putText(
                    frame,
                    "!! WARNING: Turtle Neck !!",
                    (10, y_offset),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2,
                )
                y_offset += 35

                cv2.putText(
                    frame,
                    ">>> ROS Topic: posture_status = 1",
                    (10, y_offset),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 200), 2,
                )
            else:
                cv2.putText(
                    frame,
                    "Good Posture",
                    (10, y_offset),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 230, 0), 2,
                )
                y_offset += 35

                cv2.putText(
                    frame,
                    ">>> ROS Topic: posture_status = 0",
                    (10, y_offset),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 0), 2,
                )

        else:
            # 사용자 미감지
            cv2.putText(
                frame,
                "No person detected",
                (10, y_offset),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (128, 128, 128), 2,
            )
            y_offset += 35
            cv2.putText(
                frame,
                "Please face the camera",
                (10, y_offset),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (128, 128, 128), 2,
            )

        # --- 캘리브레이션 기준 정보 (하단) ---
        cv2.putText(
            frame,
            f"Baseline: {self.baseline_angle:.1f} deg",
            (10, h_frame - 55),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1,
        )
        cv2.putText(
            frame,
            f"Warn < {self.warning_entry:.1f} | OK > {self.warning_exit:.1f}",
            (10, h_frame - 35),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1,
        )
        cv2.putText(
            frame,
            "Press 'q' to quit",
            (10, h_frame - 15),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1,
        )

        cv2.imshow("TurtleNeck Detector V2", frame)

    # ======================== 리소스 정리 ========================

    def destroy_node(self):
        """
        노드 종료 시 리소스를 정리합니다.
        - 웹캠 해제
        - MediaPipe 모델 해제
        - OpenCV 윈도우 닫기
        """
        self.get_logger().info("노드를 종료합니다. 리소스 정리 중...")
        self.is_running = False

        if self.camera_thread.is_alive():
            self.camera_thread.join(timeout=2.0)

        if self.cap is not None and self.cap.isOpened():
            self.cap.release()
            self.get_logger().info("웹캠 해제 완료.")

        if self.detector is not None:
            self.detector.close()
            self.get_logger().info("MediaPipe PoseLandmarker 해제 완료.")

        cv2.destroyAllWindows()
        self.get_logger().info("OpenCV 윈도우 닫기 완료.")

        super().destroy_node()


# ======================== 메인 함수 ========================

def main(args=None):
    """
    ROS 2 노드 진입점.

    [실행 흐름]
    1. rclpy 초기화
    2. TurtleNeckDetectorV2 노드 생성
       - 유저 아이디 입력
       - 웹캠 + MediaPipe 초기화
       - 5초 캘리브레이션
       - 카메라 쓰레드 시작
    3. spin() 으로 ROS 2 콜백 루프 진입
    4. Ctrl+C 또는 'q' 키로 종료
    5. 리소스 정리 후 rclpy 종료
    """

    rclpy.init(args=args)

    node = None
    try:
        node = TurtleNeckDetectorV2()
        rclpy.spin(node)

    except RuntimeError as e:
        # 웹캠 초기화 실패 등
        print(f"\n[FATAL] 노드 시작 실패: {e}", file=sys.stderr)

    except KeyboardInterrupt:
        # Ctrl+C 종료
        if node:
            node.get_logger().info("Ctrl+C 감지 - 종료합니다.")

    finally:
        # 리소스 정리
        if node is not None:
            node.destroy_node()

        # rclpy가 초기화된 상태에서만 shutdown 호출
        try:
            if rclpy.ok():
                rclpy.shutdown()
        except Exception:
            pass

    print("\n프로그램이 정상적으로 종료되었습니다.")


if __name__ == "__main__":
    main()

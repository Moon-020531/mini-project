"""
==========================================================================
  TurtleNeckPublisher - ROS 2 기반 거북목 감지 & 토픽 발행 노드
==========================================================================
  MediaPipe Pose의 Nose, Ear, Shoulder 3D 랜드마크를 활용하여
  Craniovertebral Angle(CVA)을 계산하고, 거북목 여부를 판별합니다.

  판별 결과는 ROS 2 토픽 'posture_status'로 발행합니다.
    - 0 : 정상 자세 (Good Posture)
    - 1 : 거북목 (Forward Head Posture / Turtle Neck)

  [실행 환경]
    - OS      : WSL2 / Ubuntu 24.04
    - ROS 2   : Jazzy Jalisco
    - 통신     : Micro-ROS Agent (Docker) → ESP32 직렬 통신

  [실행 방법]
    $ source /opt/ros/jazzy/setup.bash
    $ python3 turtle_neck_publisher.py
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

# --- CVA 판별 임계값 (히스테리시스 적용) ---
# 의학적으로 CVA가 약 50도 미만이면 전방머리자세(거북목)로 판단합니다.
# 히스테리시스를 적용하여, 경고 진입은 낮은 값(ENTRY), 해제는 높은 값(EXIT)으로 설정합니다.
CVA_WARNING_ENTRY = 50.0   # 정상 → 거북목 전환 기준 (이 각도 미만이면 경고)
CVA_WARNING_EXIT  = 55.0   # 거북목 → 정상 복귀 기준 (이 각도 초과 시 해제)

PUBLISH_INTERVAL_SEC = 0.5  # 0.5초마다 토픽 발행 (2 Hz)

# --- 통신 설정 (웹사이트 연동용) ---
BACKEND_URL = "http://localhost:8080/api/log"
LOGGING_INTERVAL_SEC = 600  # 10분마다 웹 서버로 전송

# --- 웹캠 설정 ---
CAMERA_INDEX = 1  # 웹캠 장치 인덱스 (WSL2에서 0번이 timeout 나면 1번 또는 2번 시도)

# --- MediaPipe Pose 설정 ---
MODEL_COMPLEXITY = 1       # 모델 복잡도 (0: 경량, 1: 중간, 2: 정밀)
                           # 3D 좌표 정확도를 위해 1 이상 권장
MIN_DETECTION_CONF = 0.5   # 최소 감지 신뢰도
MIN_TRACKING_CONF  = 0.5   # 최소 추적 신뢰도


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
       - 여기서 '수평'은 카메라 좌표계의 X축 방향입니다.
       - 사용자가 카메라를 정면으로 바라볼 때, X축이 좌우 방향이 됩니다.
    3) 두 벡터 사이의 각도를 구합니다:
           cos(θ) = (A · B) / (|A| × |B|)
       이때 θ가 CVA입니다.
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
    # 귀가 어깨보다 앞(카메라 쪽)에 있으면 Z 성분이 음수가 되어
    # neck_vec이 앞으로 기울어진 것으로 반영됩니다.
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
    # 이미지 좌표에서 y 값은 위로 갈수록 작아지므로, ear_y < shoulder_y이면 귀가 위.
    if ear_3d[1] < shoulder_3d[1]:
        # 정상: 귀가 어깨 위에 있음 → CVA는 양수 각도
        return cva_angle
    else:
        # 비정상: 귀가 어깨보다 아래에 있으면(극단적 전방 자세) 음수 보정
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

    MediaPipe는 x, y를 [0, 1] 범위의 정규화 좌표로,
    z를 깊이(depth) 추정값으로 제공합니다.
    - x, y: 프레임 해상도를 곱해 픽셀 좌표로 변환
    - z: 엉덩이(hip) 중심 기준의 상대적 깊이. 단위가 x, y와 다르므로
         프레임 너비를 곱해 대략적 스케일을 맞춥니다.

    Parameters
    ----------
    landmark : MediaPipe Pose 랜드마크 객체 (x, y, z, visibility 포함)
    frame_w  : 프레임 너비 (픽셀)
    frame_h  : 프레임 높이 (픽셀)

    Returns
    -------
    (px_x, px_y, scaled_z) : 3D 좌표 튜플
    """
    px_x = landmark.x * frame_w
    px_y = landmark.y * frame_h
    # z는 MediaPipe 내부 단위(hip 기준 상대 깊이)이므로,
    # x, y와 비슷한 스케일로 맞추기 위해 frame_w를 곱합니다.
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
    # Tasks API에서는 LANDMARK_NAME 대신 인덱스를 직접 사용합니다.
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

class TurtleNeckPublisher(Node):
    """
    거북목 감지 결과를 ROS 2 토픽으로 발행하는 노드.

    [토픽]
        이름   : posture_status
        타입   : std_msgs/msg/Int32
        값     : 0 = 정상, 1 = 거북목

    [동작 원리]
    1. 타이머 콜백(PUBLISH_INTERVAL_SEC 주기)으로 웹캠 프레임을 읽습니다.
    2. MediaPipe Pose로 상체 랜드마크를 추출합니다.
    3. Nose, Ear, Shoulder의 3D 좌표로 CVA를 계산합니다.
    4. 히스테리시스 로직으로 거북목 여부를 판별합니다.
    5. 판별 결과(0 또는 1)를 토픽으로 발행합니다.
    """

    def __init__(self):
        super().__init__("turtle_neck_publisher")

        # ---- 웹사이트 로깅을 위한 변수 ----
        self.username = "anonymous"  # 향후 입력받거나 파라미터로 처리 가능
        self.total_good_time = 0
        self.warning_count = 0
        self.good_posture_start_time = time.time()
        self.last_log_time = time.time()

        # ---- ROS 2 Publisher 생성 ----
        self.publisher_ = self.create_publisher(Int32, "posture_status", 10)

        # ---- 주기 타이머 생성 (ROS 2 발행용) ----
        # 토픽 발행만 0.5초 주기로 실행 (카메라 읽기는 별도 쓰레드)
        self.timer = self.create_timer(PUBLISH_INTERVAL_SEC, self._publish_status)

        # ---- 웹캠 초기화 ----
        self.cap = self._init_camera()

        # ---- MediaPipe Tasks API 초기화 ----
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

        # ---- 히스테리시스 상태 ----
        # is_warning이 True이면 현재 거북목 상태이며,
        # CVA가 CVA_WARNING_EXIT를 넘어야 정상으로 복귀합니다.
        self.is_warning = False

        # ---- 연속 감지 실패 카운터 ----
        self.no_detection_count = 0
        self.NO_DETECTION_LIMIT = 30  # 30프레임 (약 1.5초) 감지 불가 시 경고

        # ---- 쓰레드 제어 플래그 ----
        self.is_running = True
        
        # 최신 상태 저장을 위한 변수
        self.current_cva_angle = 70.0
        self.current_side_data = None

        self.get_logger().info("=" * 55)
        self.get_logger().info("  TurtleNeckPublisher 노드가 시작되었습니다.")
        self.get_logger().info(f"  토픽: 'posture_status' (Int32, 0=정상, 1=거북목)")
        self.get_logger().info(f"  웹사이트 로깅: {LOGGING_INTERVAL_SEC}초 주기")
        self.get_logger().info("=" * 55)

        # 카메라 루프를 독립된 쓰레드로 실행 (프레임 저하 방지)
        self.camera_thread = threading.Thread(target=self.run_camera_loop, daemon=True)
        self.camera_thread.start()

    def _init_camera(self) -> cv2.VideoCapture:
        """
        웹캠을 초기화하고 VideoCapture 객체를 반환합니다.

        WSL2 환경에서는 USB 웹캠을 사용하려면 다음이 필요합니다:
          1. usbipd-win 을 통한 USB 장치 연결 (Windows 호스트 측)
          2. WSL2 커널에 USB 카메라 드라이버 포함
          3. /dev/video0 장치 노드 존재 확인

        웹캠 열기에 실패하면 에러 로그를 남기고 예외를 발생시킵니다.
        """
        self.get_logger().info(
            f"웹캠(index={CAMERA_INDEX})을 여는 중..."
        )

        cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
        
        # WSL2 타임아웃 방지를 위해 포맷과 해상도를 강제 지정
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        if not cap.isOpened():
            self.get_logger().error(
                "============================================\n"
                "  웹캠을 열 수 없습니다!\n"
                "  [WSL2 체크리스트]\n"
                "  1. usbipd-win으로 USB 카메라가 WSL에 연결되었는지 확인\n"
                "     > usbipd list / usbipd attach --wsl ...\n"
                "  2. /dev/video0 장치 노드가 존재하는지 확인\n"
                "     > ls -la /dev/video*\n"
                "  3. 카메라가 다른 프로그램에서 사용 중인지 확인\n"
                "============================================"
            )
            raise RuntimeError("웹캠 초기화 실패 - 카메라를 열 수 없습니다.")

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
            url = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task"
            urllib.request.urlretrieve(url, self.model_path)
            self.get_logger().info("모델 다운로드 완료!")

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
                # 메인 쓰레드의 rclpy를 종료하기 위한 시그널
                os.kill(os.getpid(), signal.SIGINT)
                break

    def _update_posture_status(self, cva_angle: float):
        """
        히스테리시스(Hysteresis) 로직으로 거북목 상태를 갱신합니다.

        [히스테리시스 필요성]
        하나의 임계값(예: 50도)만 사용하면, CVA가 49↔51도를 반복할 때
        상태가 빈번하게 전환되어 ESP32에 불필요한 명령이 도배됩니다.

        [로직]
        - 현재 정상(is_warning=False)인 경우:
            → CVA < CVA_WARNING_ENTRY(50°) 이면 거북목으로 전환
        - 현재 거북목(is_warning=True)인 경우:
            → CVA > CVA_WARNING_EXIT(55°) 이면 정상으로 복귀
        - 그 사이(50°~55°)에서는 이전 상태를 유지합니다.
        """
        prev_status = self.is_warning

        if not self.is_warning:
            # 정상 → 거북목 전환 조건
            if cva_angle < CVA_WARNING_ENTRY:
                self.is_warning = True
        else:
            # 거북목 → 정상 복귀 조건
            if cva_angle > CVA_WARNING_EXIT:
                self.is_warning = False

        # 상태 변경 시 로그 출력
        if prev_status != self.is_warning:
            if self.is_warning:
                self.get_logger().warn(f"⚠️  거북목 감지! CVA={cva_angle:.1f}°")
                self.warning_count += 1
                self.total_good_time += (time.time() - self.good_posture_start_time)
            else:
                self.get_logger().info(f"✅ 자세 정상 복귀. CVA={cva_angle:.1f}°")
                self.good_posture_start_time = time.time()

    def _handle_backend_logging(self):
        """10분 주기로 웹사이트용 백엔드 서버에 통계 기록을 전송합니다."""
        current_time = time.time()
        if current_time - self.last_log_time >= LOGGING_INTERVAL_SEC:
            if not self.is_warning:
                self.total_good_time += (current_time - self.good_posture_start_time)
                self.good_posture_start_time = current_time

            # 비동기 전송
            threading.Thread(
                target=self._send_backend_log_async,
                args=(int(self.total_good_time), self.warning_count, self.username),
                daemon=True
            ).start()

            self.total_good_time = 0
            self.warning_count = 0
            self.last_log_time = current_time

    def _send_backend_log_async(self, good_time, warning_count, username):
        try:
            data = {
                "goodPostureTime": good_time,
                "warningCount": warning_count,
                "username": username
            }
            requests.post(BACKEND_URL, json=data, timeout=3)
            self.get_logger().info(f"[백엔드 로깅 완료] 좋은 자세: {good_time}초 / 경고: {warning_count}회")
        except Exception:
            pass # 백엔드 꺼져있으면 무시

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

    def _visualize(
        self,
        frame,
        cva_angle: float | None,
        side_data: dict | None,
    ):
        """
        OpenCV 윈도우에 CVA 각도, 랜드마크, 판별 결과를 시각화합니다.

        Parameters
        ----------
        frame     : BGR 이미지 프레임
        cva_angle : CVA 각도 (None이면 감지 실패)
        side_data : get_best_side_landmarks()의 반환값 (None이면 감지 실패)
        """
        y_offset = 30

        if cva_angle is not None and side_data is not None:
            ear_2d = side_data["ear_2d"]
            sho_2d = side_data["shoulder_2d"]
            side   = side_data["side"]
            vis    = side_data["visibility"]

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
                    "!! TURTLE NECK DETECTED !!",
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

        # --- 안내 텍스트 (하단) ---
        h = frame.shape[0]
        cv2.putText(
            frame,
            "Press 'q' to quit",
            (10, h - 15),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1,
        )

        cv2.imshow("TurtleNeck Detector (ROS 2)", frame)

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
    2. TurtleNeckPublisher 노드 생성
    3. spin() 으로 콜백 루프 진입
    4. Ctrl+C 또는 'q' 키로 종료
    5. 리소스 정리 후 rclpy 종료
    """

    rclpy.init(args=args)

    node = None
    try:
        node = TurtleNeckPublisher()
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

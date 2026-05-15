"""
==========================================================================
  ROS 2 Web Bridge (Flask -> ROS 2 Publisher)
==========================================================================
  이 스크립트는 React 웹 프론트엔드에서 오는 HTTP 요청을 받아서
  ROS 2 토픽('posture_status')으로 변환하여 발행(Publish)합니다.

  [설치 필요 라이브러리]
  $ pip install flask flask-cors

  [실행 방법]
  $ source /opt/ros/jazzy/setup.bash
  $ python3 ros2_web_bridge.py
==========================================================================
"""

import sys
import threading
import logging
from flask import Flask, jsonify
from flask_cors import CORS

import rclpy
from rclpy.node import Node
from std_msgs.msg import Int32

# Flask 앱 설정
app = Flask(__name__)
# 웹(React)에서 다른 포트로 요청을 보내더라도 허용(CORS)
CORS(app)

# Flask 기본 로그 숨기기 (터미널 깔끔하게)
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# ROS 2 노드 전역 변수
ros_node = None

class WebBridgeNode(Node):
    def __init__(self):
        super().__init__('ros2_web_bridge')
        self.publisher_ = self.create_publisher(Int32, 'posture_status', 10)
        self.get_logger().info('Web Bridge 노드가 시작되었습니다.')
        self.get_logger().info('토픽: posture_status (0=정상, 1=거북목)')

    def publish_status(self, is_warning: bool):
        msg = Int32()
        msg.data = 1 if is_warning else 0
        self.publisher_.publish(msg)
        status_str = "거북목 경고(1)" if is_warning else "정상(0)"
        self.get_logger().info(f'웹 요청 수신 -> ROS 2 토픽 발행: {status_str}')


# ==================== Flask 엔드포인트 ====================

@app.route('/warning', methods=['GET', 'POST'])
def handle_warning():
    """웹에서 거북목이 감지되었을 때 호출되는 엔드포인트"""
    if ros_node is not None:
        ros_node.publish_status(is_warning=True)
    return jsonify({"status": "success", "message": "warning published"})

@app.route('/normal', methods=['GET', 'POST'])
def handle_normal():
    """웹에서 정상 자세로 복귀했을 때 호출되는 엔드포인트"""
    if ros_node is not None:
        ros_node.publish_status(is_warning=False)
    return jsonify({"status": "success", "message": "normal published"})

# ==================== 메인 실행부 ====================

def run_flask():
    print("===========================================")
    print("  ROS 2 Web Bridge 서버 실행 중...")
    print("  웹 브라우저의 요청 대기 중 (포트 5000)")
    print("===========================================")
    # 모든 IP에서 접근 가능하도록 host='0.0.0.0'
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

def main(args=None):
    global ros_node

    # ROS 2 초기화
    rclpy.init(args=args)
    ros_node = WebBridgeNode()

    # Flask 서버를 백그라운드 쓰레드로 실행
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # ROS 2 콜백 루프 실행 (메인 쓰레드)
    try:
        rclpy.spin(ros_node)
    except KeyboardInterrupt:
        ros_node.get_logger().info('Ctrl+C 입력으로 브릿지를 종료합니다.')
    finally:
        if ros_node:
            ros_node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        sys.exit(0)

if __name__ == '__main__':
    main()

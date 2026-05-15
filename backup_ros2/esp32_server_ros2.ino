/*
 * ==========================================================================
 *   ESP32 Micro-ROS 거북목 알림 장치 (Subscriber Node)
 * ==========================================================================
 *
 *   [기능]
 *   ROS 2 토픽 'posture_status'를 구독(Subscribe)하여,
 *   파이썬 노드(turtle_neck_publisher)가 보내는 자세 판별 결과에 따라
 *   LED와 부저를 제어합니다.
 *
 *   [토픽]
 *     이름 : posture_status
 *     타입 : std_msgs/msg/Int32
 *     값   : 0 = 정상 자세 → 초록 LED ON, 부저 OFF
 *            1 = 거북목    → 빨간 LED ON, 부저 ON
 *
 *   [통신 방식]
 *   USB 시리얼 → Docker Micro-ROS Agent → ROS 2 네트워크
 *
 *   [Micro-ROS Agent 실행 (WSL2/Docker)]
 *   $ docker run -it --rm \
 *       -v /dev:/dev \
 *       --privileged \
 *       --net=host \
 *       microros/micro-ros-agent:jazzy \
 *       serial --dev /dev/ttyUSB0 -b 115200
 *
 *   [하드웨어 핀 배선]
 *     GPIO 13 → 초록 LED (+) → 220Ω 저항 → GND
 *     GPIO 14 → 빨간 LED (+) → 220Ω 저항 → GND
 *     GPIO 12 → 피에조 부저 (+) → GND
 *
 * ==========================================================================
 */

#include <micro_ros_arduino.h>

#include <stdio.h>
#include <rcl/rcl.h>
#include <rcl/error_handling.h>
#include <rclc/rclc.h>
#include <rclc/executor.h>
#include <std_msgs/msg/int32.h>

// ======================== 하드웨어 핀 설정 ========================
// 기존 esp32_server.ino와 동일한 핀 배치를 유지합니다.
#define GREEN_LED_PIN  13   // 초록 LED: 정상 자세 표시
#define RED_LED_PIN    14   // 빨간 LED: 거북목 경고 표시
#define BUZZER_PIN     12   // 피에조 부저: 거북목 경고 알림음

// ======================== 부저 설정 ========================
#define BUZZER_FREQ    1000  // 경고음 주파수 (Hz)
#define BUZZER_CHANNEL 0     // ESP32 LEDC 채널 (tone 대체)

// ======================== Micro-ROS 객체 ========================
rcl_subscription_t subscriber;          // 구독자(Subscriber) 객체
std_msgs__msg__Int32 msg;               // 수신 메시지 버퍼
rclc_executor_t executor;               // 실행기(Executor)
rclc_support_t support;                 // 지원 구조체
rcl_allocator_t allocator;              // 메모리 할당기
rcl_node_t node;                        // 노드 객체

// ======================== 상태 관리 ========================
// 현재 자세 상태를 저장합니다. (초기값: 정상)
int current_posture = 0;

// Micro-ROS Agent 연결 상태
enum AgentState {
  WAITING_AGENT,      // Agent 연결 대기 중
  AGENT_AVAILABLE,    // Agent 감지됨 → 초기화 진행
  AGENT_CONNECTED,    // Agent 연결 완료 → 정상 동작 중
  AGENT_DISCONNECTED  // Agent 연결 끊김 → 재연결 시도
};
AgentState state = WAITING_AGENT;

// ======================== 에러 처리 매크로 ========================
/*
 * Micro-ROS 함수 호출 결과를 검사하는 매크로입니다.
 * RCL_RET_OK가 아니면 에러 처리 코드를 실행합니다.
 *
 * RCCHECK  : 실패 시 error_loop()에 진입 (복구 불가능한 에러)
 * RCSOFTCHECK : 실패 시 무시하고 계속 진행 (일시적 에러)
 */
#define RCCHECK(fn) { rcl_ret_t temp_rc = fn; if (temp_rc != RCL_RET_OK) { error_loop(); } }
#define RCSOFTCHECK(fn) { rcl_ret_t temp_rc = fn; (void)temp_rc; }


// ======================== 함수 정의 ========================

/**
 * 복구 불가능한 에러 발생 시 빨간 LED를 빠르게 깜빡이며 무한 대기합니다.
 * 이 상태에서는 ESP32를 리셋(EN 버튼)해야 합니다.
 */
void error_loop() {
  while (1) {
    digitalWrite(RED_LED_PIN, !digitalRead(RED_LED_PIN));
    delay(100);
  }
}

/**
 * LED를 정상 자세 상태로 설정합니다.
 *   - 초록 LED: ON
 *   - 빨간 LED: OFF
 *   - 부저: OFF
 */
void set_normal_posture() {
  digitalWrite(GREEN_LED_PIN, HIGH);
  digitalWrite(RED_LED_PIN, LOW);
  ledcWrite(BUZZER_PIN, 0);  // 부저 끄기
  Serial.println("[ROS2] 상태: 바른 자세 (GREEN LED ON)");
}

/**
 * LED를 거북목 경고 상태로 설정합니다.
 *   - 초록 LED: OFF
 *   - 빨간 LED: ON
 *   - 부저: ON (1kHz)
 */
void set_warning_posture() {
  digitalWrite(GREEN_LED_PIN, LOW);
  digitalWrite(RED_LED_PIN, HIGH);
  ledcWrite(BUZZER_PIN, 128);  // 부저 켜기 (듀티 50%)
  Serial.println("[ROS2] 상태: 거북목 감지! (RED LED ON, BUZZER ON)");
}

/**
 * Agent 연결 대기 중임을 나타내는 LED 패턴입니다.
 * 초록 LED가 느리게 깜빡입니다.
 */
void set_waiting_pattern() {
  static unsigned long last_blink = 0;
  if (millis() - last_blink > 500) {
    digitalWrite(GREEN_LED_PIN, !digitalRead(GREEN_LED_PIN));
    digitalWrite(RED_LED_PIN, LOW);
    last_blink = millis();
  }
}

/**
 * [핵심] ROS 2 토픽 수신 콜백 함수
 *
 * 파이썬 노드(turtle_neck_publisher)가 'posture_status' 토픽으로
 * 메시지를 발행할 때마다 이 함수가 자동으로 호출됩니다.
 *
 * @param msgin  수신된 메시지 포인터 (std_msgs/msg/Int32)
 *               - data == 0 : 정상 자세
 *               - data == 1 : 거북목 감지
 */
void subscription_callback(const void * msgin) {
  const std_msgs__msg__Int32 * incoming = (const std_msgs__msg__Int32 *)msgin;

  int received_value = incoming->data;

  // 상태가 변경되었을 때만 하드웨어를 제어합니다.
  // (동일한 값이 반복 수신되어도 LED/부저를 불필요하게 재설정하지 않음)
  if (received_value != current_posture) {
    current_posture = received_value;

    if (current_posture == 0) {
      set_normal_posture();
    } else {
      set_warning_posture();
    }
  }
}

/**
 * Micro-ROS Agent가 응답하는지 확인합니다.
 * @return true: Agent 감지됨, false: Agent 없음
 */
bool check_agent() {
  // 100ms 타임아웃으로 Agent ping 전송
  return (RMW_RET_OK == rmw_uros_ping_agent(100, 1));
}

/**
 * Micro-ROS 노드, 구독자, 실행기를 초기화합니다.
 *
 * [초기화 순서]
 * 1. support 구조체 초기화 (ROS 2 컨텍스트 생성)
 * 2. 노드 생성: "esp32_posture_node"
 * 3. 구독자 생성: 토픽 "posture_status", 타입 Int32
 * 4. 실행기(Executor) 초기화 및 구독자 콜백 등록
 */
bool create_entities() {
  allocator = rcl_get_default_allocator();

  // 1. Support 초기화
  RCCHECK(rclc_support_init(&support, 0, NULL, &allocator));

  // 2. 노드 생성 (노드 이름: "esp32_posture_node")
  RCCHECK(rclc_node_init_default(
    &node,
    "esp32_posture_node",   // 노드 이름
    "",                     // 네임스페이스 (빈 문자열 = 기본)
    &support
  ));

  // 3. 구독자 생성
  //    토픽: "posture_status"
  //    메시지 타입: std_msgs/msg/Int32
  //    QoS: best_effort (실시간성 우선, 가끔 메시지 유실 허용)
  RCCHECK(rclc_subscription_init_best_effort(
    &subscriber,
    &node,
    ROSIDL_GET_MSG_TYPE_SUPPORT(std_msgs, msg, Int32),
    "posture_status"
  ));

  // 4. 실행기(Executor) 초기화
  //    핸들 수 = 1 (구독자 1개)
  RCCHECK(rclc_executor_init(&executor, &support.context, 1, &allocator));

  // 구독자에 콜백 함수 등록
  RCCHECK(rclc_executor_add_subscription(
    &executor,
    &subscriber,
    &msg,
    &subscription_callback,
    ON_NEW_DATA   // 새 데이터가 도착했을 때만 콜백 실행
  ));

  return true;
}

/**
 * Micro-ROS 엔티티(노드, 구독자, 실행기)를 정리합니다.
 * Agent 연결이 끊겼을 때 호출되어 리소스를 해제한 후
 * 재연결을 시도합니다.
 */
void destroy_entities() {
  rmw_context_t * rmw_context = rcl_context_get_rmw_context(&support.context);
  (void) rmw_uros_set_context_entity_destroy_session_timeout(rmw_context, 0);

  RCSOFTCHECK(rcl_subscription_fini(&subscriber, &node));
  RCSOFTCHECK(rcl_node_fini(&node));
  RCSOFTCHECK(rclc_support_fini(&support));
  rclc_executor_fini(&executor);
}


// ======================== Arduino 메인 함수 ========================

void setup() {
  // 시리얼 통신 초기화 (Micro-ROS Agent와의 통신용)
  Serial.begin(115200);

  // 하드웨어 핀 모드 설정
  pinMode(GREEN_LED_PIN, OUTPUT);
  pinMode(RED_LED_PIN, OUTPUT);

  // ESP32 LEDC를 이용한 부저 초기화
  // (Arduino의 tone() 대신 ESP32 전용 LEDC PWM 사용)
  ledcAttach(BUZZER_PIN, BUZZER_FREQ, 8);  // 핀 12, 1kHz, 8비트 해상도
  ledcWrite(BUZZER_PIN, 0);  // 초기에는 부저 끄기

  // 초기 LED 상태: 모두 끄기
  digitalWrite(GREEN_LED_PIN, LOW);
  digitalWrite(RED_LED_PIN, LOW);

  // Micro-ROS 시리얼 전송 설정
  // USB 케이블을 통해 WSL2의 Docker Agent와 통신합니다.
  set_microros_transports();

  // Agent 연결 대기 상태에서 시작
  state = WAITING_AGENT;

  Serial.println("===========================================");
  Serial.println("  ESP32 Micro-ROS 거북목 알림 장치");
  Serial.println("  토픽: posture_status (Int32)");
  Serial.println("  대기 중: Micro-ROS Agent 연결을 기다립니다...");
  Serial.println("===========================================");
}

void loop() {
  /*
   * [상태 머신 (State Machine)]
   *
   * 4가지 상태를 순환하며 Agent 연결을 관리합니다:
   *
   *   WAITING_AGENT  ──(Agent 감지)──→  AGENT_AVAILABLE
   *         ↑                                  │
   *         │                          (엔티티 생성 성공)
   *         │                                  ↓
   *   AGENT_DISCONNECTED  ←──(ping 실패)──  AGENT_CONNECTED
   *         │                                  │
   *         └────(엔티티 정리 후)──→  WAITING_AGENT
   */

  switch (state) {

    // ---- 상태 1: Agent 연결 대기 ----
    case WAITING_AGENT:
      // 초록 LED 느리게 깜빡이며 Agent를 기다립니다.
      set_waiting_pattern();

      if (check_agent()) {
        Serial.println("[Micro-ROS] Agent 감지! 초기화를 시작합니다...");
        state = AGENT_AVAILABLE;
      }
      break;

    // ---- 상태 2: Agent 감지됨 → 엔티티 생성 ----
    case AGENT_AVAILABLE:
      if (create_entities()) {
        Serial.println("[Micro-ROS] 노드 및 구독자 생성 완료!");
        Serial.println("[Micro-ROS] 'posture_status' 토픽 수신 대기 중...");

        // 연결 성공 표시: 초록 LED 3번 빠르게 깜빡임
        for (int i = 0; i < 3; i++) {
          digitalWrite(GREEN_LED_PIN, HIGH);
          delay(150);
          digitalWrite(GREEN_LED_PIN, LOW);
          delay(150);
        }

        // 초기 상태: 정상 자세 (초록 LED ON)
        set_normal_posture();
        state = AGENT_CONNECTED;
      } else {
        // 엔티티 생성 실패 → 다시 대기 상태로
        state = WAITING_AGENT;
      }
      break;

    // ---- 상태 3: 정상 동작 중 ----
    case AGENT_CONNECTED:
      // 실행기(Executor)를 통해 콜백 함수를 처리합니다.
      // 새 메시지가 도착하면 subscription_callback()이 자동 호출됩니다.
      RCSOFTCHECK(rclc_executor_spin_some(&executor, RCL_MS_TO_NS(100)));

      // 주기적으로 Agent가 살아있는지 확인합니다.
      if (!check_agent()) {
        Serial.println("[Micro-ROS] Agent 연결이 끊겼습니다!");
        state = AGENT_DISCONNECTED;
      }
      break;

    // ---- 상태 4: Agent 연결 끊김 → 정리 후 재연결 ----
    case AGENT_DISCONNECTED:
      Serial.println("[Micro-ROS] 리소스 정리 후 재연결을 시도합니다...");
      destroy_entities();

      // 연결 끊김 표시: 빨간 LED 3번 깜빡임
      for (int i = 0; i < 3; i++) {
        digitalWrite(RED_LED_PIN, HIGH);
        delay(200);
        digitalWrite(RED_LED_PIN, LOW);
        delay(200);
      }

      state = WAITING_AGENT;
      break;

    default:
      break;
  }

  // 코어 안정을 위한 최소 딜레이
  delay(10);
}

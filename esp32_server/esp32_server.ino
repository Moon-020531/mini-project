/*
 * ==========================================================================
 * ESP32 Micro-ROS 거북목 알림 장치 (Wi-Fi Subscriber Node)
 * ==========================================================================
 *
 * [기능]
 * ROS 2 토픽 'posture_status'를 구독(Subscribe)하여,
 * 파이썬 노드(turtle_neck_detector_v2)가 보내는 자세 판별 결과에 따라
 * LED와 부저를 제어합니다.
 *
 * [통신 방식]
 * Wi-Fi를 통한 Micro-ROS Agent(UDP) 통신
 *
 * [Micro-ROS Agent 실행 (PC 터미널)]
 * $ docker run -it --rm -p 8888:8888/udp microros/micro-ros-agent:jazzy udp4 --port 8888
 *
 * ==========================================================================
 */

#include <micro_ros_arduino.h>
#include <WiFi.h>

#include <stdio.h>
#include <rcl/rcl.h>
#include <rcl/error_handling.h>
#include <rclc/rclc.h>
#include <rclc/executor.h>
#include <std_msgs/msg/int32.h>

// ======================== Wi-Fi 및 Agent 설정 ========================
const char* ssid = "5층";
const char* password = "48864886";

// PC(WSL2가 실행되는 윈도우 호스트)의 IP 주소
const char* agent_ip_str = "192.168.0.45";
const uint16_t agent_port = 8888;

// ======================== 하드웨어 핀 설정 ========================
#define GREEN_LED_PIN  13   // 초록 LED: 정상 자세 표시
#define RED_LED_PIN    14   // 빨간 LED: 거북목 경고 표시
#define BUZZER_PIN     12   // 피에조 부저: 거북목 경고 알림음

// 부저 설정 (LEDC)
#define BUZZER_FREQ    1000  // 경고음 주파수 (Hz)
#define BUZZER_CHANNEL 0     // ESP32 LEDC 채널

// ======================== Micro-ROS 객체 ========================
rcl_subscription_t subscriber;
std_msgs__msg__Int32 msg;
rclc_executor_t executor;
rclc_support_t support;
rcl_allocator_t allocator;
rcl_node_t node;

// ======================== 상태 관리 ========================
int current_posture = 0;

enum AgentState {
  WAITING_AGENT,      // Agent 연결 대기 중
  AGENT_AVAILABLE,    // Agent 감지됨 → 초기화 진행
  AGENT_CONNECTED,    // Agent 연결 완료 → 정상 동작 중
  AGENT_DISCONNECTED  // Agent 연결 끊김 → 재연결 시도
};
AgentState state = WAITING_AGENT;

// 🌟 수정된 에러 처리 매크로: 실패 시 무한루프 대신 false 반환 🌟
#define RCCHECK(fn) { rcl_ret_t temp_rc = fn; if (temp_rc != RCL_RET_OK) { Serial.println("[에러] Micro-ROS 통신 지연. 재시도합니다..."); return false; } }
#define RCSOFTCHECK(fn) { rcl_ret_t temp_rc = fn; (void)temp_rc; }

// ======================== 함수 정의 ========================

void set_normal_posture() {
  digitalWrite(GREEN_LED_PIN, HIGH);
  digitalWrite(RED_LED_PIN, LOW);
  ledcWrite(BUZZER_PIN, 0);  // 부저 끄기
  Serial.println("[ROS2] 상태: 바른 자세 (GREEN LED ON)");
}

void set_warning_posture() {
  digitalWrite(GREEN_LED_PIN, LOW);
  digitalWrite(RED_LED_PIN, HIGH);
  ledcWrite(BUZZER_PIN, 128);  // 부저 켜기 (듀티 50%)
  Serial.println("[ROS2] 상태: 거북목 감지! (RED LED ON, BUZZER ON)");
}

void set_waiting_pattern() {
  static unsigned long last_blink = 0;
  if (millis() - last_blink > 500) {
    digitalWrite(GREEN_LED_PIN, !digitalRead(GREEN_LED_PIN));
    digitalWrite(RED_LED_PIN, LOW);
    last_blink = millis();
  }
}

// 토픽 수신 콜백 함수
void subscription_callback(const void * msgin) {
  const std_msgs__msg__Int32 * incoming = (const std_msgs__msg__Int32 *)msgin;
  int received_value = incoming->data;

  if (received_value != current_posture) {
    current_posture = received_value;
    if (current_posture == 0) {
      set_normal_posture();
    } else {
      set_warning_posture();
    }
  }
}

bool check_agent() {
  return (RMW_RET_OK == rmw_uros_ping_agent(300, 1));
}

bool create_entities() {
  allocator = rcl_get_default_allocator();

  RCCHECK(rclc_support_init(&support, 0, NULL, &allocator));

  RCCHECK(rclc_node_init_default(
    &node,
    "esp32_posture_node",
    "",
    &support
  ));

  RCCHECK(rclc_subscription_init_best_effort(
    &subscriber,
    &node,
    ROSIDL_GET_MSG_TYPE_SUPPORT(std_msgs, msg, Int32),
    "posture_status"
  ));

  RCCHECK(rclc_executor_init(&executor, &support.context, 1, &allocator));

  RCCHECK(rclc_executor_add_subscription(
    &executor,
    &subscriber,
    &msg,
    &subscription_callback,
    ON_NEW_DATA
  ));

  return true;
}

void destroy_entities() {
  rmw_context_t * rmw_context = rcl_context_get_rmw_context(&support.context);
  (void) rmw_uros_set_context_entity_destroy_session_timeout(rmw_context, 0);

  RCSOFTCHECK(rcl_subscription_fini(&subscriber, &node));
  RCSOFTCHECK(rcl_node_fini(&node));
  RCSOFTCHECK(rclc_support_fini(&support));
  rclc_executor_fini(&executor);
}

// ======================== Arduino 메인 ========================

void setup() {
  Serial.begin(115200);

  pinMode(GREEN_LED_PIN, OUTPUT);
  pinMode(RED_LED_PIN, OUTPUT);

  ledcAttach(BUZZER_PIN, BUZZER_FREQ, 8);
  ledcWrite(BUZZER_PIN, 0);

  digitalWrite(GREEN_LED_PIN, LOW);
  digitalWrite(RED_LED_PIN, LOW);

  // Wi-Fi 수동 연결 및 상태 출력
  Serial.println();
  Serial.print("[Wi-Fi] '"); Serial.print(ssid); Serial.println("' 네트워크에 연결 시도 중...");
  
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    digitalWrite(GREEN_LED_PIN, !digitalRead(GREEN_LED_PIN)); // 연결 중 초록불 토글
  }
  
  Serial.println();
  Serial.println("[Wi-Fi] 연결 성공!");
  Serial.print("[Wi-Fi] ESP32 IP 주소: ");
  Serial.println(WiFi.localIP());

  // Micro-ROS Wi-Fi 전송 설정
  Serial.print("[Micro-ROS] Agent IP: "); Serial.println(agent_ip_str);
  Serial.print("[Micro-ROS] Agent Port: "); Serial.println(agent_port);

  // 이미 Wi-Fi에 연결되어 있으므로 아래 함수는 전송 계층만 설정합니다.
  set_microros_wifi_transports((char*)ssid, (char*)password, (char*)agent_ip_str, agent_port);

  state = WAITING_AGENT;

  Serial.println("===========================================");
  Serial.println("  ESP32 Micro-ROS 거북목 알림 장치 (Wi-Fi)");
  Serial.println("===========================================");
}

void loop() {
  switch (state) {
    case WAITING_AGENT:
      {
        set_waiting_pattern();
        
        static unsigned long last_debug_time = 0;
        if (millis() - last_debug_time > 2000) {
          // IP 주소를 192.168.0.45 로 변경 적용
          Serial.println("[Micro-ROS] PC의 Agent(192.168.0.45:8888) 응답을 기다리는 중...");
          last_debug_time = millis();
        }

        if (check_agent()) {
          Serial.println("[Micro-ROS] Agent 감지 완료! 통신 초기화를 시작합니다...");
          state = AGENT_AVAILABLE;
        }
      }
      break;

    case AGENT_AVAILABLE:
      if (create_entities()) {
        Serial.println("[Micro-ROS] 노드 및 구독자 생성 완료!");
        Serial.println("[Micro-ROS] 'posture_status' 토픽 수신 대기 중...");

        for (int i = 0; i < 3; i++) {
          digitalWrite(GREEN_LED_PIN, HIGH);
          delay(150);
          digitalWrite(GREEN_LED_PIN, LOW);
          delay(150);
        }

        set_normal_posture();
        state = AGENT_CONNECTED;
      } else {
        // 🌟 찌꺼기 리소스 정리 후 재연결 유도
        destroy_entities();
        state = WAITING_AGENT;
      }
      break;

    case AGENT_CONNECTED:
      RCSOFTCHECK(rclc_executor_spin_some(&executor, RCL_MS_TO_NS(100)));

      if (!check_agent()) {
        Serial.println("[Micro-ROS] Agent 연결이 끊겼습니다!");
        state = AGENT_DISCONNECTED;
      }
      break;

    case AGENT_DISCONNECTED:
      Serial.println("[Micro-ROS] 리소스 정리 후 재연결을 시도합니다...");
      destroy_entities();

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

  delay(10);
}
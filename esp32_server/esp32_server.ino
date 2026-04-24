#include <WiFi.h>
#include <WebServer.h>

// 1. Wi-Fi 설정
const char* ssid = "5층";
const char* password = "48864886";

// 2. 핀 및 하드웨어 설정 (네오픽셀 대신 일반 LED 2개 사용)
#define GREEN_LED_PIN 13  // 초록색 LED (+) 연결 핀
#define RED_LED_PIN   14  // 빨간색 LED (+) 연결 핀
#define BUZZER_PIN    12  // 피에조 부저 (+) 연결 핀

// 웹 서버(포트 80) 객체 생성
WebServer server(80);

// ★ 프론트엔드(React 웹 브라우저) 호환성을 위한 CORS 헤더 함수
void sendCORSHeaders() {
  server.sendHeader("Access-Control-Allow-Origin", "*"); // 모든 위치에서의 통신 허용
  server.sendHeader("Access-Control-Allow-Methods", "POST, GET, OPTIONS");
  server.sendHeader("Access-Control-Allow-Headers", "Content-Type");
}

// 정상 상태 (/normal) 요청이 들어왔을 때 실행할 함수
void handleNormal() {
  digitalWrite(GREEN_LED_PIN, HIGH);
  digitalWrite(RED_LED_PIN, LOW);
  noTone(BUZZER_PIN); 
  
  sendCORSHeaders(); // 응답 전송 전에 CORS 헤더 추가
  server.send(200, "text/plain", "Status: NORMAL - Green LED ON, Red LED OFF, Buzzer OFF");
  Serial.println("상태: 바른 자세 (GREEN LED ON)");
}

// 거북목 경고 상태 (/warning) 요청이 들어왔을 때 실행할 함수
void handleWarning() {
  digitalWrite(RED_LED_PIN, HIGH);
  digitalWrite(GREEN_LED_PIN, LOW);
  tone(BUZZER_PIN, 1000); 
  
  sendCORSHeaders(); // 응답 전송 전에 CORS 헤더 추가
  server.send(200, "text/plain", "Status: WARNING - Red LED ON, Green LED OFF, Buzzer ON");
  Serial.println("상태: 거북목 감지! (RED LED ON)");
}

// 웹 브라우저(React)가 전송하는 OPTIONS(사전 검증) 요청을 처리하는 함수
void handleOptions() {
  sendCORSHeaders();
  server.send(204); 
}

void setup() {
  Serial.begin(115200);
  
  // 핀들을 출력(OUTPUT) 모드로 설정
  pinMode(GREEN_LED_PIN, OUTPUT);
  pinMode(RED_LED_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);

  // 초기 상태는 모든 LED와 부저 끄기
  digitalWrite(GREEN_LED_PIN, LOW);
  digitalWrite(RED_LED_PIN, LOW);
  noTone(BUZZER_PIN);

  // Wi-Fi 연결 시도 
  // Wi-Fi 자동 재연결 설정
  WiFi.setAutoReconnect(true);
  WiFi.persistent(true);

  Serial.println();
  Serial.print("Wi-Fi 연결 중: ");
  Serial.println(ssid);
  WiFi.begin(ssid, password);
  
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  
  Serial.println("");
  Serial.println("Wi-Fi 연결 성공!");
  
  // ★중요: 할당받은 IP 주소 출력 (이 주소가 리액트 코드의 ESP32_URL과 일치해야 함)
  Serial.print("ESP32 IP 주소: ");
  Serial.println(WiFi.localIP()); 

  // 라우팅 (경로 지정)
  server.on("/normal", handleNormal);
  server.on("/warning", handleWarning);
  
  // 찾을 수 없는 경로(또는 OPTIONS 요청)가 들어왔을 때의 처리
  server.onNotFound([]() {
    if (server.method() == HTTP_OPTIONS) {
      handleOptions(); 
    } else {
      sendCORSHeaders();
      server.send(404, "text/plain", "Not Found");
    }
  });
  
  // 웹 서버 시작
  server.begin();
  Serial.println("HTTP 웹 서버 대기 중...");
}

void loop() {
  // Wi-Fi 끊김 감지 및 자동 재연결
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Wi-Fi 연결이 끊겼습니다. 재연결 시도 중...");
    WiFi.disconnect();
    WiFi.begin(ssid, password);
    unsigned long startAttemptTime = millis();
    
    // 5초간 재연결 대기
    while (WiFi.status() != WL_CONNECTED && millis() - startAttemptTime < 5000) {
      delay(500);
      Serial.print(".");
    }
    if (WiFi.status() == WL_CONNECTED) {
      Serial.println("\nWi-Fi 재연결 성공! IP: " + WiFi.localIP().toString());
    } else {
      Serial.println("\n재연결 실패. 다음 루프에서 다시 시도합니다.");
    }
  }

  // 클라이언트(React 대시보드)의 접속 요청 처리
  server.handleClient();
  delay(10); // 코어 안정을 위한 짧은 딜레이
}

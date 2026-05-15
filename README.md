# 🌿 ZenDesk (스마트 데스크 대시보드)

사용자의 자세와 눈 건강을 실시간으로 분석하여 거북목을 예방하고 최적의 작업 환경을 제공하는 **IoT 기반 스마트 데스크 시스템**입니다.

웹캠을 통한 고성능 비전 분석, IoT 디바이스 연동 피드백, 그리고 누적된 데이터를 통한 정밀한 건강 리포트를 제공합니다.

---

## ✨ 주요 기능

### 1. 고성능 실시간 자세 분석 (AI Vision)
- **거북목 감지 (CVA Analysis):** MediaPipe Pose를 활용해 귀와 어깨의 각도(CVA)를 계산하여 거북목을 정밀 판별합니다.
- **어깨 비대칭 모니터링:** 양쪽 어깨의 수평도를 분석하여 척추 건강을 체크합니다.
- **모니터 거리 측정:** 사용자의 얼굴 크기를 기반으로 모니터와의 적정 거리 유지 여부를 감지합니다.

### 2. 눈 피로도 및 깜빡임 감지 (Ocular Health)
- **눈 깜빡임 카운팅:** MediaPipe Face Mesh와 EAR(Eye Aspect Ratio) 수식을 사용하여 실시간 깜빡임 횟수를 측정합니다.
- **피로도 경고:** 분당 깜빡임 횟수가 정상 수치(15회) 이하로 떨어지거나 눈을 오랫동안 뜨고 있을 경우 "인공눈물 점안 및 휴식 권장" 경고를 보냅니다.
- **최적화 엔진:** 비동기 모델 실행 로직을 적용하여 브라우저 환경에서도 끊김 없는 60FPS 모니터링을 실현했습니다.

### 3. IoT 하드웨어 연동 (Smart Feedback)
- **즉각적인 경고:** 거북목 또는 피로 상태 감지 시 ESP32 디바이스를 통해 LED(Red)와 부저로 사용자에게 즉각적인 피드백을 제공합니다.
- **상태 표시:** 올바른 자세 유지 시 Green LED를 통해 긍정적인 강화 학습을 유도합니다.

---

## 🏗 기술 스택

### Frontend
- **Framework:** React, Vite
- **UI/UX:** Vanilla CSS (Glassmorphism), Lucide React
- **Vision AI:** MediaPipe (Pose, Face Mesh), TensorFlow.js
- **Communication:** Axios, WebSocket/HTTP

### Backend
- **Framework:** Spring Boot 3
- **Language:** Java 17
- **Database:** MySQL 8.0, Spring Data JPA
- **Security:** Spring Security, OAuth 2.0 (Google, Naver)

### IoT & AI Script
- **Hardware:** ESP32 (Arduino C++)
- **Python:** OpenCV, MediaPipe (Legacy/Backup 지원용)

---

## 🚀 시작하기

### 1. 백엔드 설정
1. MySQL에 `turtle_neck_db` 데이터베이스 생성
2. `backend/src/main/resources/application.properties` 설정 확인
3. OAuth 2.0 API 키 입력 (`application-secret.properties` 생성 필요)

### 2. 프론트엔드 설정
1. `frontend` 디렉토리에서 `npm install`
2. `.env.local` 파일에 클라이언트 ID 설정

### 3. 하드웨어 설정 (선택 사항)
1. `esp32_server.ino`를 ESP32 보드에 업로드 (고정 IP: 192.168.0.21)

### 4. 실행
- **Backend:** `cd backend && ./gradlew bootRun`
- **Frontend:** `cd frontend && npm run dev`

---

## 📸 대시보드 미리보기

- **실시간 분석:** 카메라 화면 상단에 한글 HUD(자세 점수, EAR, 깜빡임) 실시간 시각화
- **사이드 위젯:** 포커스 타이머, 눈 피로도 모니터, 실시간 자세 분석 차트 배치
- **기록 확인:** 캘린더의 각 날짜를 클릭하여 과거의 정밀한 자세 건강 점수 확인

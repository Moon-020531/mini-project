import socket
import sys

def test():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('0.0.0.0', 8888))
    print("========================================", flush=True)
    print("  UDP 통신 테스트 중... (포트 8888)", flush=True)
    print("  ESP32에서 오는 신호를 기다립니다...", flush=True)
    print("========================================", flush=True)
    print("★ 만약 'Windows 보안 경보' 창이 뜨면 반드시 '허용'을 눌러주세요! ★", flush=True)
    
    while True:
        try:
            data, addr = s.recvfrom(1024)
            print(f">>> 성공!!! ESP32({addr[0]})에서 신호가 도착했습니다: {data.hex()}", flush=True)
            print("네트워크 연결은 정상입니다. 이제 이 창을 닫아주세요.", flush=True)
        except Exception as e:
            print(f"오류: {e}", flush=True)

if __name__ == "__main__":
    test()

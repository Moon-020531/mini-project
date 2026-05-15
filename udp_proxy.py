import socket
import threading
import subprocess

def get_wsl_ip():
    try:
        # Get WSL IP by running hostname -I inside WSL
        result = subprocess.run(['wsl', 'bash', '-c', 'hostname -I'], capture_output=True, text=True)
        return result.stdout.split()[0].strip()
    except Exception as e:
        print("Failed to get WSL IP, defaulting to 127.0.0.1:", e)
        return '127.0.0.1'

def forward_udp():
    wsl_ip = get_wsl_ip()
    target = (wsl_ip, 8888)
    
    # Bind to all Windows interfaces (including 192.168.0.3)
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(('0.0.0.0', 8888))
    
    client_map = {}

    print(f"UDP Proxy started: Listening on Windows 0.0.0.0:8888, forwarding to WSL2 {wsl_ip}:8888")

    def recv_from_wsl():
        wsl_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        wsl_sock.bind(('0.0.0.0', 0)) # Ephemeral port for return traffic
        return wsl_sock

    wsl_socket = recv_from_wsl()

    def wsl_to_esp32():
        while True:
            try:
                data, addr = wsl_socket.recvfrom(4096)
                # WSL sent data back. Forward it to the last known ESP32 address.
                if client_map:
                    for esp_addr in list(client_map.keys()):
                        server.sendto(data, esp_addr)
                        # print(f"<- Forwarded {len(data)} bytes from WSL to ESP32 {esp_addr}")
            except Exception as e:
                print(f"Error in wsl_to_esp32: {e}")

    threading.Thread(target=wsl_to_esp32, daemon=True).start()

    while True:
        try:
            data, addr = server.recvfrom(4096)
            if addr not in client_map:
                print(f"New connection from ESP32 at {addr}")
                client_map[addr] = True
            
            # Forward ESP32 data to WSL2
            # print(f"-> Forwarded {len(data)} bytes from ESP32 to WSL {target}")
            wsl_socket.sendto(data, target)
        except Exception as e:
            print(f"Error in forward_udp: {e}")

if __name__ == "__main__":
    forward_udp()

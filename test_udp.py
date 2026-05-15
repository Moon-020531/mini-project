import socket

def test_udp_receive():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', 8888))
    print("Listening on UDP 8888 for ESP32 pings...")
    
    # We will just wait for ONE packet and print its details
    try:
        data, addr = sock.recvfrom(1024)
        print(f"SUCCESS: Received {len(data)} bytes from ESP32 at {addr}")
        print(f"Data (hex): {data.hex()}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_udp_receive()

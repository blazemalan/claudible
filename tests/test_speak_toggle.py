import os
import socket
import subprocess
import tempfile
import threading
from pathlib import Path

def test_speak_toggle_sends_toggle():
    # Create a temporary directory to host the socket
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_path = os.path.join(tmpdir, "claudible.sock")

        # Thread function to run the server
        received_data = b""
        def server_thread():
            nonlocal received_data
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(socket_path)
            server.listen(1)
            server.settimeout(2.0) # Prevent hanging forever
            try:
                conn, _ = server.accept()
                with conn:
                    received_data = conn.recv(1024)
            except socket.timeout:
                pass
            finally:
                server.close()

        # Start the background server thread
        t = threading.Thread(target=server_thread)
        t.start()

        # Wait briefly for the socket to be created
        import time
        for _ in range(20):
            if os.path.exists(socket_path):
                break
            time.sleep(0.05)

        # Run the script with CLAUDIBLE_SOCKET pointing to our tmp socket
        script_path = Path(__file__).parent.parent / "scripts" / "speak-toggle.sh"
        env = os.environ.copy()
        env["CLAUDIBLE_SOCKET"] = socket_path

        result = subprocess.run([str(script_path)], env=env, capture_output=True, text=True)

        # Wait for the server thread to finish
        t.join(timeout=3.0)

        # Assertions
        assert result.returncode == 0
        assert received_data == b"toggle"


def test_speak_toggle_no_socket():
    with tempfile.TemporaryDirectory() as tmpdir:
        socket_path = os.path.join(tmpdir, "nonexistent.sock")

        script_path = Path(__file__).parent.parent / "scripts" / "speak-toggle.sh"
        env = os.environ.copy()
        env["CLAUDIBLE_SOCKET"] = socket_path

        result = subprocess.run([str(script_path)], env=env, capture_output=True, text=True)

        # Should exit cleanly (return code 0) and not error
        assert result.returncode == 0

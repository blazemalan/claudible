import json
import os
import shutil
import socket
import subprocess
import threading
import pytest

# Ensure jq is installed
if shutil.which("jq") is None:
    pytest.skip("jq not installed", allow_module_level=True)

# The tts-capture.sh script relies on tail -r which is a macOS feature.
# We'll create a fake tail command in a temp dir and put it on the PATH for the subprocess.
def create_fake_tail(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tail_path = bin_dir / "tail"
    tail_path.write_text(
        "#!/bin/bash\n"
        'if [ "$1" = "-r" ]; then\n'
        "  shift\n"
        '  tac "$@"\n'
        "else\n"
        '  /usr/bin/tail "$@"\n'
        "fi\n"
    )
    tail_path.chmod(0o755)
    return bin_dir

def start_socket_server(sock_path, timeout=5.0):
    """
    Starts a listening unix socket on another thread and returns a list
    that will contain the received message.
    """
    received = []

    def server_thread():
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(sock_path))
        server.listen(1)
        server.settimeout(timeout)
        try:
            conn, _ = server.accept()
            with conn:
                data = conn.recv(1024)
                if data:
                    received.append(data)
        except socket.timeout:
            pass
        finally:
            server.close()

    thread = threading.Thread(target=server_thread)
    thread.start()
    return received, thread

def test_happy_path(tmp_path):
    # Setup paths
    capture_file = tmp_path / "claude-last-response.txt"
    socket_path = tmp_path / "claudible.sock"
    transcript_file = tmp_path / "transcript.jsonl"

    # Create the fake tail command
    bin_dir = create_fake_tail(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["CLAUDIBLE_CAPTURE_FILE"] = str(capture_file)
    env["CLAUDIBLE_SOCKET"] = str(socket_path)

    # Create transcript
    assistant_msg = "This is a sufficiently long message from the assistant that should exceed the fifty character limit easily."
    # The script uses tr '\n' ' ' on the text
    transcript_content = [
        {"type": "user", "message": {"content": [{"type": "text", "text": "Hello"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "This is a sufficiently long message\nfrom the assistant that should exceed\nthe fifty character limit easily."}]}}
    ]
    with open(transcript_file, "w") as f:
        for msg in transcript_content:
            f.write(json.dumps(msg) + "\n")

    # Setup socket server
    received, server_thread = start_socket_server(socket_path)

    # Run the script
    script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts", "tts-capture.sh"))
    input_data = json.dumps({"transcript_path": str(transcript_file)})

    result = subprocess.run(
        ["bash", script_path],
        input=input_data.encode("utf-8"),
        env=env,
        capture_output=True
    )

    assert result.returncode == 0
    server_thread.join(timeout=2.0)

    # Verify capture file
    assert capture_file.exists()
    content = capture_file.read_text().strip()
    assert content == "This is a sufficiently long message from the assistant that should exceed the fifty character limit easily."

    # Verify socket message
    assert len(received) == 1
    assert received[0] == b"prefetch"

def test_short_message(tmp_path):
    capture_file = tmp_path / "claude-last-response.txt"
    socket_path = tmp_path / "claudible.sock"
    transcript_file = tmp_path / "transcript.jsonl"

    bin_dir = create_fake_tail(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["CLAUDIBLE_CAPTURE_FILE"] = str(capture_file)
    env["CLAUDIBLE_SOCKET"] = str(socket_path)

    transcript_content = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Too short."}]}}
    ]
    with open(transcript_file, "w") as f:
        for msg in transcript_content:
            f.write(json.dumps(msg) + "\n")

    received, server_thread = start_socket_server(socket_path, timeout=1.0)

    script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts", "tts-capture.sh"))
    input_data = json.dumps({"transcript_path": str(transcript_file)})

    result = subprocess.run(
        ["bash", script_path],
        input=input_data.encode("utf-8"),
        env=env,
        capture_output=True
    )

    assert result.returncode == 0
    server_thread.join(timeout=2.0)

    assert not capture_file.exists()
    assert len(received) == 0

def test_tool_result_first(tmp_path):
    """
    If a tool_result appears before the newest assistant text in the newest-first scan
    (which means the tool_result is chronologically AFTER the assistant text),
    the script will NOT capture the assistant text, because it sets seen_tool_result=1
    and skips it.
    """
    capture_file = tmp_path / "claude-last-response.txt"
    socket_path = tmp_path / "claudible.sock"
    transcript_file = tmp_path / "transcript.jsonl"

    bin_dir = create_fake_tail(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["CLAUDIBLE_CAPTURE_FILE"] = str(capture_file)
    env["CLAUDIBLE_SOCKET"] = str(socket_path)

    transcript_content = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "This is a long enough message before a tool result." * 2}]}},
        {"type": "tool_result", "message": "result"}
    ]
    with open(transcript_file, "w") as f:
        for msg in transcript_content:
            f.write(json.dumps(msg) + "\n")

    received, server_thread = start_socket_server(socket_path, timeout=1.0)

    script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts", "tts-capture.sh"))
    input_data = json.dumps({"transcript_path": str(transcript_file)})

    result = subprocess.run(
        ["bash", script_path],
        input=input_data.encode("utf-8"),
        env=env,
        capture_output=True
    )

    assert result.returncode == 0
    server_thread.join(timeout=2.0)

    assert not capture_file.exists(), "Capture file should not exist if a tool_result was seen first"
    assert len(received) == 0, "No prefetch should be sent if a tool_result was seen first"

def test_missing_socket(tmp_path):
    capture_file = tmp_path / "claude-last-response.txt"
    # Provide a socket path that does NOT exist
    socket_path = tmp_path / "nonexistent.sock"
    transcript_file = tmp_path / "transcript.jsonl"

    bin_dir = create_fake_tail(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["CLAUDIBLE_CAPTURE_FILE"] = str(capture_file)
    env["CLAUDIBLE_SOCKET"] = str(socket_path)

    transcript_content = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "This is a sufficiently long message from the assistant that should exceed the fifty character limit easily."}]}}
    ]
    with open(transcript_file, "w") as f:
        for msg in transcript_content:
            f.write(json.dumps(msg) + "\n")

    script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts", "tts-capture.sh"))
    input_data = json.dumps({"transcript_path": str(transcript_file)})

    result = subprocess.run(
        ["bash", script_path],
        input=input_data.encode("utf-8"),
        env=env,
        capture_output=True
    )

    assert result.returncode == 0

    assert capture_file.exists()
    content = capture_file.read_text().strip()
    assert content == "This is a sufficiently long message from the assistant that should exceed the fifty character limit easily."

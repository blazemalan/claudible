import ast
from pathlib import Path

def test_socket_protocol_branches():
    main_py_path = Path("app/main.py")
    assert main_py_path.exists(), "app/main.py not found"

    source = main_py_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(main_py_path))

    found_prefetch = False
    found_toggle = False
    found_toggle_selection = False

    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            # Check if this compare involves the string literals
            # Typically looks like `head == "prefetch"`
            nodes_to_check = [node.left] + node.comparators
            for n in nodes_to_check:
                if isinstance(n, ast.Constant) and n.value == "prefetch":
                    found_prefetch = True
                elif isinstance(n, ast.Constant) and n.value == "toggle":
                    found_toggle = True
                elif isinstance(n, ast.Constant) and n.value == "toggle-selection":
                    found_toggle_selection = True

    assert found_prefetch, 'socket "prefetch" branch missing from main.py — the tts capture path depends on it'
    assert found_toggle, 'socket "toggle" branch missing from main.py — the Cmd+Option+S hotkey path depends on it'
    assert found_toggle_selection, 'socket "toggle-selection" branch missing from main.py — the speak-selection path depends on it'

def test_client_scripts_send_correct_heads():
    tts_capture_path = Path("scripts/tts-capture.sh")
    assert tts_capture_path.exists(), "scripts/tts-capture.sh not found"
    tts_content = tts_capture_path.read_text(encoding="utf-8")
    assert "b'prefetch'" in tts_content or "'prefetch'" in tts_content or "prefetch" in tts_content, "scripts/tts-capture.sh must send 'prefetch'"

    speak_toggle_path = Path("scripts/speak-toggle.sh")
    assert speak_toggle_path.exists(), "scripts/speak-toggle.sh not found"
    speak_toggle_content = speak_toggle_path.read_text(encoding="utf-8")
    assert "b'toggle'" in speak_toggle_content or "'toggle'" in speak_toggle_content or "toggle" in speak_toggle_content, "scripts/speak-toggle.sh must send 'toggle'"

    speak_selection_path = Path("scripts/speak-selection.sh")
    assert speak_selection_path.exists(), "scripts/speak-selection.sh not found"
    speak_selection_content = speak_selection_path.read_text(encoding="utf-8")
    assert "b'toggle-selection'" in speak_selection_content, "scripts/speak-selection.sh must send 'toggle-selection'"

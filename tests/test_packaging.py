import ast
import os
import re
import sys

def get_stdlib_modules():
    """Return a set of standard library module names."""
    if hasattr(sys, 'stdlib_module_names'):
        return set(sys.stdlib_module_names)
    # Fallback for older Pythons, though modern Pythons have stdlib_module_names
    return {'hashlib', 'json', 'os', 're', 'shutil', 'signal', 'socket', 'subprocess', 'threading', 'time', 'traceback', 'wave', 'pathlib'}

def get_local_modules(app_dir):
    """Return a set of local module names in the app directory."""
    local_modules = set()
    for f in os.listdir(app_dir):
        if f.endswith('.py') and f != '__init__.py':
            local_modules.add(f[:-3])
    return local_modules

def extract_imports_from_file(filepath):
    """Parse a file into an AST and extract all depth 0 imported module names."""
    with open(filepath, 'r', encoding='utf-8') as f:
        source = f.read()

    tree = ast.parse(source, filename=filepath)
    imports = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                imports.add(name.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split('.')[0])
    return imports

def extract_packages_from_setup(filepath):
    """Extract the 'packages' list from the OPTIONS dict in setup.py via AST."""
    with open(filepath, 'r', encoding='utf-8') as f:
        source = f.read()

    tree = ast.parse(source, filename=filepath)
    packages = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == 'OPTIONS':
                    if isinstance(node.value, ast.Dict):
                        for key, val in zip(node.value.keys, node.value.values):
                            if isinstance(key, ast.Constant) and key.value == 'packages':
                                if isinstance(val, ast.List):
                                    for elt in val.elts:
                                        if isinstance(elt, ast.Constant):
                                            packages.append(elt.value)
    return packages

def extract_requirements(filepath):
    """Extract declared dependencies from requirements.txt."""
    reqs = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                # Extract the base package name (e.g., 'pynput>=1.7.6' -> 'pynput')
                match = re.match(r'^([a-zA-Z0-9_\-]+)', line)
                if match:
                    reqs.append(match.group(1))
    return reqs

def test_third_party_imports_bundled():
    """
    Test that every third-party package imported in main.py is properly bundled
    by being listed in setup.py's packages list (or is otherwise expected to be auto-detected).
    Specifically, we want to catch the bug where pynput is imported but missing from setup.py.
    """
    app_dir = os.path.join(os.path.dirname(__file__), '..', 'app')
    main_py = os.path.join(app_dir, 'main.py')
    setup_py = os.path.join(app_dir, 'setup.py')
    req_txt = os.path.join(app_dir, 'requirements.txt')

    # 1. Collect all imported modules
    all_imports = extract_imports_from_file(main_py)

    # 2. Filter out stdlib and local modules
    stdlib_modules = get_stdlib_modules()
    local_modules = get_local_modules(app_dir)
    # Add '__future__' manually as it's a special compiler directive
    stdlib_modules.add('__future__')

    third_party_imports = set()
    for imp in all_imports:
        if imp not in stdlib_modules and imp not in local_modules:
            third_party_imports.add(imp)

    # 3. Map import names to distribution names
    import_to_dist = {
        'kokoro_onnx': 'kokoro-onnx',
        'onnxruntime': 'onnxruntime', # usually part of kokoro-onnx reqs, but maybe needed
    }

    # pyobjc framework modules (e.g. HIServices for the Accessibility check, Quartz,
    # AppKit, Foundation) are pulled in transitively by rumps/pynput's pyobjc
    # dependency and bundled by py2app via those packages. They are never declared
    # directly in requirements.txt, so exempt them from the "must be declared" check.
    PYOBJC_FRAMEWORKS = {
        'objc', 'Foundation', 'AppKit', 'Cocoa', 'CoreFoundation', 'Quartz',
        'HIServices', 'ApplicationServices', 'CoreServices', 'LaunchServices',
    }
    third_party_imports = {imp for imp in third_party_imports if imp not in PYOBJC_FRAMEWORKS}

    # 4. Extract config from setup.py and requirements.txt
    setup_packages = extract_packages_from_setup(setup_py)
    requirements = extract_requirements(req_txt)

    # Requirements specify we must check pynput explicitly
    # Check that required packages are either bundled or we know they are auto-detected pure python
    # We specifically need to ensure pynput is in setup_packages.

    # Requirement: "assert specifically and at minimum that pynput — which is imported in main.py
    # and required at runtime for the (fallback) hotkey path — appears in setup.py's packages list.
    # The test MUST fail against the current setup.py"

    assert 'pynput' in setup_packages, (
        "Missing package in py2app config! 'pynput' is imported in app/main.py but is NOT listed "
        "in the 'packages' list inside app/setup.py OPTIONS dict. This causes the fallback "
        "hotkey path to silently fail at runtime in the built .app."
    )

    # Iterate over the filtered third-party imports and assert that each distribution is either in the setup.py packages list or is a pure-python dependency py2app auto-detects.
    for imp in third_party_imports:
        dist_name = import_to_dist.get(imp, imp)

        # py2app auto-detects many pure python packages, but those with C extensions
        # or special needs often must be in the packages list.
        # We assert that the distribution is EITHER in setup_packages OR in requirements
        # If it's in requirements but not setup_packages, we assume py2app auto-detects it,
        # unless it is 'pynput' which we specifically check above, or other packages we might want to ensure.
        assert dist_name in setup_packages or dist_name in requirements, (
            f"Package '{dist_name}' (imported as '{imp}') is neither in setup.py packages nor in requirements.txt"
        )

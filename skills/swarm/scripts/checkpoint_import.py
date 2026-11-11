"""Import-check driver for checkpointed Python modules. Stdlib only."""

IMPORT_DRIVER = r"""
import importlib, importlib.util, json, os, sys, traceback
sys.dont_write_bytecode = True
root = os.path.abspath(sys.argv[1])
dir_name = sys.argv[2]
files = sys.argv[3:]
sys.path.insert(0, root)
if os.path.isdir(os.path.join(root, "src")):
    sys.path.insert(1, os.path.join(root, "src"))
sandbox_prefix = os.path.abspath(os.path.join(root, dir_name, "sandboxes"))
sandbox_roots = []
for rel in files:
    path = os.path.abspath(os.path.join(root, rel))
    if path.startswith(sandbox_prefix + os.sep):
        parts = os.path.relpath(path, sandbox_prefix).split(os.sep)
        if len(parts) >= 3:
            folder = os.path.join(sandbox_prefix, *parts[:2])
            source = os.path.join(folder, "src")
            source = source if os.path.isdir(source) else folder
            if source not in sandbox_roots:
                sandbox_roots.append(source)
sys.path[:0] = sandbox_roots
failures = []
for rel in files:
    path = os.path.join(root, rel)
    module_root = root
    if path.startswith(sandbox_prefix + os.sep):
        parts_from_root = os.path.relpath(path, sandbox_prefix).split(os.sep)
        if len(parts_from_root) >= 3:
            folder = os.path.join(sandbox_prefix, *parts_from_root[:2])
            module_root = os.path.join(folder, "src")
            if not os.path.isdir(module_root):
                module_root = folder
    module_rel = os.path.relpath(path, module_root).replace("\\", "/")
    parts = module_rel[:-3].split("/")
    if parts[0] == "src" and os.path.isdir(os.path.join(root, "src")):
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if parts and parts[-1] == "__main__":
        continue  # importing it runs the program; the syntax check covers it
    try:
        if parts and all(p.isidentifier() for p in parts):
            importlib.import_module(".".join(parts))
        else:
            name = "_slcheck_" + "_".join(p.replace("-", "_") for p in parts)
            sys.path.append(os.path.dirname(path))
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
    except BaseException as e:
        line = 0
        for fr in traceback.extract_tb(e.__traceback__):
            if os.path.abspath(fr.filename) == os.path.abspath(path):
                line = fr.lineno
        failures.append({"file": rel, "line": line, "error": f"{type(e).__name__}: {e}"[:150]})
print(json.dumps(failures))
"""

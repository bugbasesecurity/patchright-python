"""Apply the networkidle ts-morph patch to coreBundle.js inside built patchright
wheels, then fix the wheel RECORD so the wheel stays install-valid.

patchright-python bundles a vanilla driver (its stealth is Python-layer), so the
driver-level networkidle exclusion has to be added to the bundled driver at build
time. We do it with ts-morph (patch_bundled_networkidle.mjs) rather than a string
replace, and rewrite RECORD so pip/uv accept the modified wheel.

Usage: python patch_wheels_networkidle.py <wheel-or-dir> [more...]
"""
import base64
import hashlib
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

CORE = "patchright/driver/package/lib/coreBundle.js"
MJS = Path(__file__).with_name("patch_bundled_networkidle.mjs")


def record_hash(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).decode().rstrip("=")


def patch_wheel(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as z:
        infos = z.infolist()  # keep ZipInfo so we preserve Unix perms (driver/node is +x)
        if CORE not in z.namelist():
            raise SystemExit(f"{wheel.name}: {CORE} not found in wheel")
        contents = {zi.filename: z.read(zi.filename) for zi in infos}

    with tempfile.TemporaryDirectory() as td:
        core_path = Path(td) / "coreBundle.js"
        core_path.write_bytes(contents[CORE])
        subprocess.run(["node", str(MJS), str(core_path)], check=True)
        patched = core_path.read_bytes()
    contents[CORE] = patched

    record_name = next(n for n in contents if n.endswith(".dist-info/RECORD"))
    new_line = f"{CORE},{record_hash(patched)},{len(patched)}"
    rows = []
    for line in contents[record_name].decode().splitlines():
        rows.append(new_line if line.startswith(CORE + ",") else line)
    contents[record_name] = ("\n".join(rows) + "\n").encode()

    tmp = wheel.with_suffix(".whl.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for zi in infos:  # reuse original ZipInfo -> keeps external_attr (Unix perms)
            zout.writestr(zi, contents[zi.filename])
    tmp.replace(wheel)
    print(f"patched networkidle into {wheel.name} (perms preserved)")


def main() -> None:
    targets: list[Path] = []
    for arg in sys.argv[1:]:
        p = Path(arg)
        targets.extend(sorted(p.glob("*.whl")) if p.is_dir() else [p])
    if not targets:
        raise SystemExit("no wheels given")
    for wheel in targets:
        patch_wheel(wheel)


if __name__ == "__main__":
    main()

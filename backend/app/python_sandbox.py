"""Run Python previews in a bounded, network-disabled Docker container."""
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.schemas import SimulationResult
from app.simulator import simulate_python

MAX_PYTHON_OUTPUT_BYTES = 20_000
SANDBOX_START_TIMEOUT_SECONDS = 18
SANDBOX_IMAGE = "bug-hunter-python-sandbox:1"

_SANDBOX_SCRIPT = (
    "ulimit -f 1024; "
    "export TMPDIR=/sandbox; "
    "timeout -k 1s 3s python -I -B /input/submission.py "
    ">/sandbox/stdout.txt 2>/sandbox/stderr.txt; "
    "program_status=$?; "
    'if [ "$program_status" -eq 124 ] || [ "$program_status" -eq 137 ]; '
    "then exit 124; fi; "
    'if [ "$program_status" -eq 153 ]; then head -c 20000 /sandbox/stdout.txt; exit 73; fi; '
    'if [ "$program_status" -ne 0 ]; '
    "then head -c 3000 /sandbox/stderr.txt; exit 72; fi; "
    "head -c 20001 /sandbox/stdout.txt; "
    "exit 0"
)


def _sandbox_command(docker: str, source_directory: Path) -> list[str]:
    mount = f"type=bind,source={source_directory.resolve()},target=/input,readonly"
    return [
        docker,
        "run",
        "--rm",
        "--pull=never",
        "--init",
        "--interactive",
        "--network=none",
        "--read-only",
        "--memory=128m",
        "--memory-swap=128m",
        "--cpus=0.5",
        "--pids-limit=16",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=65534:65534",
        "--stop-timeout=1",
        "--ulimit=cpu=6:6",
        "--ulimit=fsize=1024:1024",
        "--ulimit=nofile=64:64",
        "--ulimit=core=0:0",
        "--tmpfs=/sandbox:rw,exec,nosuid,nodev,size=16m,mode=1777",
        "--mount",
        mount,
        "--workdir=/sandbox",
        "--entrypoint=/bin/sh",
        SANDBOX_IMAGE,
        "-lc",
        _SANDBOX_SCRIPT,
    ]


def _restricted_fallback(source: str, simulation_input: str) -> SimulationResult:
    result = simulate_python(source, simulation_input)
    return result.model_copy(
        update={
            "message": (
                f"{result.message} Full Python execution is unavailable because "
                "the isolated Python sandbox image is not running."
            )
        }
    )


def simulate_python_sandbox(source: str, simulation_input: str) -> SimulationResult:
    docker = shutil.which("docker")
    if docker is None:
        return _restricted_fallback(source, simulation_input)

    try:
        with tempfile.TemporaryDirectory(prefix="bug-hunter-python-") as directory:
            source_directory = Path(directory)
            (source_directory / "submission.py").write_text(source, encoding="utf-8")
            stdin = simulation_input
            if stdin and not stdin.endswith(("\n", "\r")):
                stdin += "\n"
            result = subprocess.run(
                _sandbox_command(docker, source_directory),
                input=stdin.encode("utf-8"),
                capture_output=True,
                check=False,
                timeout=SANDBOX_START_TIMEOUT_SECONDS,
            )
    except subprocess.TimeoutExpired:
        return SimulationResult(
            status="error",
            message="The Python sandbox exceeded its startup time limit.",
        )
    except OSError:
        return _restricted_fallback(source, simulation_input)

    if result.returncode == 125:
        return _restricted_fallback(source, simulation_input)
    if result.returncode == 124:
        return SimulationResult(
            status="error",
            message="The Python program exceeded its execution time limit in the sandbox.",
        )
    if result.returncode == 73:
        return SimulationResult(
            status="unsupported",
            output=result.stdout[:MAX_PYTHON_OUTPUT_BYTES].decode(
                "utf-8", errors="replace"
            ),
            message="The Python program exceeded the sandbox output limit.",
        )
    if result.returncode == 72:
        error_output = result.stdout.decode("utf-8", errors="replace").strip()
        return SimulationResult(
            status="error",
            output=error_output,
            message="The Python program stopped with a runtime error in the sandbox.",
        )
    if result.returncode != 0:
        return SimulationResult(
            status="unsupported",
            message=(
                "The Python sandbox could not start. Build the documented "
                "bug-hunter-python-sandbox:1 image."
            ),
        )

    output_bytes = result.stdout
    output = output_bytes[:MAX_PYTHON_OUTPUT_BYTES].decode("utf-8", errors="replace")
    if len(output_bytes) > MAX_PYTHON_OUTPUT_BYTES:
        return SimulationResult(
            status="unsupported",
            output=output,
            message="The Python program exceeded the sandbox output limit.",
        )
    return SimulationResult(
        status="completed",
        output=output,
        message=(
            "Python source was checked by Python and Ruff, then run in a "
            "restricted, network-disabled container."
        ),
    )

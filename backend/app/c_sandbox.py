import shutil
import subprocess
import tempfile
from pathlib import Path

from app.schemas import SimulationResult

MAX_C_OUTPUT_BYTES = 20_000
SANDBOX_START_TIMEOUT_SECONDS = 18
SANDBOX_IMAGE = "bug-hunter-c-sandbox:1"

_SANDBOX_SCRIPT = (
    "set -o pipefail; "
    "export TMPDIR=/sandbox; "
    "timeout -k 1s 8s gcc -Wall -Wextra -Wpedantic "
    "/input/submission.c -lm -o /sandbox/program >/dev/null 2>&1; "
    "compile_status=$?; "
    'if [ "$compile_status" -ne 0 ]; then '
    'if [ "$compile_status" -eq 124 ] || [ "$compile_status" -eq 137 ]; '
    "then exit 124; else exit 70; fi; fi; "
    "ulimit -t 3; ulimit -f 1024; ulimit -n 64; "
    "timeout -k 1s 3s /sandbox/program 2>/dev/null | head -c 20001; "
    "run_status=$?; "
    'if [ "$run_status" -eq 124 ] || [ "$run_status" -eq 137 ]; '
    "then exit 124; fi; "
    'if [ "$run_status" -ge 128 ]; then exit 72; fi; exit 0'
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
        "--entrypoint=/bin/bash",
        SANDBOX_IMAGE,
        "-lc",
        _SANDBOX_SCRIPT,
    ]


def simulate_c_sandbox(source: str, simulation_input: str) -> SimulationResult:
    docker = shutil.which("docker")
    if docker is None:
        return SimulationResult(
            status="unsupported",
            message=(
                "The C output test was not run because Docker is unavailable. "
                "Install Docker and build the documented sandbox image."
            ),
        )

    try:
        with tempfile.TemporaryDirectory(prefix="bug-hunter-c-") as directory:
            source_directory = Path(directory)
            (source_directory / "submission.c").write_text(source, encoding="utf-8")
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
            message=(
                "The C sandbox exceeded its startup or total time limit; "
                "the expected output could not be verified."
            ),
        )
    except OSError:
        return SimulationResult(
            status="unsupported",
            message=(
                "The C sandbox could not start. Ensure Docker is running and "
                "the documented sandbox image is installed."
            ),
        )

    if result.returncode == 124:
        return SimulationResult(
            status="error",
            message=(
                "The C program exceeded its compile or execution time limit "
                "inside the sandbox."
            ),
        )
    if result.returncode == 70:
        return SimulationResult(
            status="error",
            message="The sandbox compiler could not build this C source.",
        )
    if result.returncode == 72:
        return SimulationResult(
            status="error",
            message="The C program stopped unexpectedly inside the sandbox.",
        )
    if result.returncode != 0:
        return SimulationResult(
            status="unsupported",
            message=(
                "The C sandbox could not start. Ensure Docker is running and "
                "the documented sandbox image is installed."
            ),
        )

    output_bytes = result.stdout
    output = output_bytes[:MAX_C_OUTPUT_BYTES].decode("utf-8", errors="replace")
    if len(output_bytes) > MAX_C_OUTPUT_BYTES:
        return SimulationResult(
            status="unsupported",
            output=output,
            message=(
                "The C program exceeded the sandbox output limit; "
                "its expected output was not verified."
            ),
        )
    return SimulationResult(
        status="completed",
        output=output,
        message=(
            "The C program was compiled and run inside a restricted, "
            "network-disabled container."
        ),
    )

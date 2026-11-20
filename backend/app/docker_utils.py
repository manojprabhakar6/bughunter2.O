"""Locate the Docker CLI on PATH or at the standard Windows install path."""
import os
import shutil
from pathlib import Path


def find_docker() -> str | None:
    docker = shutil.which("docker")
    if docker:
        return docker
    if os.name != "nt":
        return None
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    candidate = (
        program_files
        / "Docker"
        / "Docker"
        / "resources"
        / "bin"
        / "docker.exe"
    )
    return str(candidate) if candidate.is_file() else None

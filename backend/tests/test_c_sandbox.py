import subprocess

from app import c_sandbox


def test_c_sandbox_uses_container_limits_and_passes_standard_input(monkeypatch):
    captured = {}
    monkeypatch.setattr(c_sandbox.shutil, "which", lambda _: "docker")

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs["input"]
        return subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=b"Hello, Ada!\n",
            stderr=b"",
        )

    monkeypatch.setattr(c_sandbox.subprocess, "run", fake_run)

    result = c_sandbox.simulate_c_sandbox("int main(void) { return 0; }\n", "Ada")

    assert result.status == "completed"
    assert result.output == "Hello, Ada!\n"
    assert captured["input"] == b"Ada\n"
    for option in (
        "--pull=never",
        "--network=none",
        "--read-only",
        "--memory=128m",
        "--cpus=0.5",
        "--pids-limit=16",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
    ):
        assert option in captured["command"]
    assert any("target=/input,readonly" in item for item in captured["command"])


def test_c_sandbox_fails_closed_when_docker_is_unavailable(monkeypatch):
    monkeypatch.setattr(c_sandbox.shutil, "which", lambda _: None)

    result = c_sandbox.simulate_c_sandbox("int main(void) { return 0; }\n", "")

    assert result.status == "unsupported"
    assert "Docker is unavailable" in result.message


def test_c_sandbox_caps_captured_output(monkeypatch):
    monkeypatch.setattr(c_sandbox.shutil, "which", lambda _: "docker")
    monkeypatch.setattr(
        c_sandbox.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            returncode=0,
            stdout=b"x" * (c_sandbox.MAX_C_OUTPUT_BYTES + 1),
            stderr=b"",
        ),
    )

    result = c_sandbox.simulate_c_sandbox("int main(void) { return 0; }\n", "")

    assert result.status == "unsupported"
    assert len(result.output.encode("utf-8")) == c_sandbox.MAX_C_OUTPUT_BYTES
    assert "output limit" in result.message

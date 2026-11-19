import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import SimulationResult


def register(
    client: TestClient,
    email: str = "test@example.com",
) -> dict[str, str]:
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "safe-test-password"},
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['accessToken']}"}


def test_submission_analyzes_and_compares_without_running_code(tmp_path, monkeypatch):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))

    with TestClient(app) as client:
        headers = register(client)
        response = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": "import os\n\nprint(missing_value)\n",
                "programGoal": "Print a greeting to the user.",
                "filename": "example.py",
                "language": "python",
            },
        )

    assert response.status_code == 201
    submission = response.json()
    assert submission["language"] == "python"
    assert submission["originalCode"] == "import os\n\nprint(missing_value)\n"
    assert submission["correctedCode"] == "print(missing_value)\n"
    assert any(finding["code"] == "F821" for finding in submission["findings"])
    assert any(
        finding["code"] == "F401" and finding["fixed"]
        for finding in submission["findings"]
    )
    assert "-import os\n" in submission["comparison"]
    assert submission["simulation"]["status"] == "error"
    assert "missing_value" in submission["simulation"]["message"]


def test_submission_persists_safe_preview_and_accepts_new_input(tmp_path, monkeypatch):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))

    with TestClient(app) as client:
        headers = register(client)
        created = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": 'value = int(input("Number: "))\nprint(value * 2)\n',
                "programGoal": "Double the entered number.",
                "simulationInput": "21",
            },
        )
        submission_id = created.json()["id"]
        loaded = client.get(f"/api/submissions/{submission_id}", headers=headers)
        updated = client.put(
            f"/api/submissions/{submission_id}",
            headers=headers,
            json={
                "code": 'value = int(input("Number: "))\nprint(value + 1)\n',
                "programGoal": "Add one to the entered number.",
                "simulationInput": "41",
            },
        )

    assert created.status_code == 201
    assert created.json()["simulation"] == {
        "status": "completed",
        "output": "Number: 21\n42\n",
        "message": (
            "Restricted preview completed; only supported AST operations were "
            "interpreted."
        ),
        "testPassed": None,
    }
    assert loaded.json()["simulation"] == created.json()["simulation"]
    assert updated.status_code == 200
    assert updated.json()["simulation"]["output"] == "Number: 41\n42\n"
    assert updated.json()["simulationInput"] == "41"


def test_expected_output_accepts_only_verified_python_corrections(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))

    with TestClient(app) as client:
        headers = register(client)
        passed = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": "import os\nprint(5)\n",
                "programGoal": "Print five.",
                "expectedOutput": "5",
            },
        )
        failed = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": "import os\nprint(5)\n",
                "programGoal": "Print five.",
                "expectedOutput": "4",
            },
        )
        unsupported_preview = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": "import os\ndef value():\n    return 1\nprint(value())\n",
                "programGoal": "Print one.",
                "expectedOutput": "1",
            },
        )
        loaded = client.get(
            f"/api/submissions/{passed.json()['id']}",
            headers=headers,
        )
        updated = client.put(
            f"/api/submissions/{passed.json()['id']}",
            headers=headers,
            json={
                "code": "print(6)\n",
                "programGoal": "Print six.",
                "expectedOutput": "6",
            },
        )
        patched = client.patch(
            f"/api/submissions/{passed.json()['id']}",
            headers=headers,
            json={"corrected_code": "print(7)\n"},
        )

    assert passed.status_code == 201
    assert passed.json()["correctedCode"] == "print(5)\n"
    assert passed.json()["simulation"]["testPassed"] is True
    assert passed.json()["expectedOutput"] == "5"
    assert loaded.json()["expectedOutput"] == "5"
    assert updated.status_code == 200
    assert updated.json()["expectedOutput"] == "6"
    assert updated.json()["simulation"]["testPassed"] is True
    assert patched.status_code == 200
    assert patched.json()["expectedOutput"] == "6"
    assert patched.json()["simulation"]["testPassed"] is False
    assert failed.status_code == 201
    assert failed.json()["correctedCode"] == "import os\nprint(5)\n"
    assert failed.json()["simulation"]["testPassed"] is False
    assert any(
        finding["code"] == "TEST_OUTPUT_MISMATCH"
        for finding in failed.json()["findings"]
    )
    assert any(
        finding["code"] == "F401" and not finding["fixed"]
        for finding in failed.json()["findings"]
    )
    assert unsupported_preview.status_code == 201
    assert unsupported_preview.json()["correctedCode"] == (
        "def value():\n    return 1\nprint(value())\n"
    )
    assert unsupported_preview.json()["simulation"]["testPassed"] is None
    assert any(
        finding["code"] == "TEST_NOT_VERIFIED"
        for finding in unsupported_preview.json()["findings"]
    )


def test_large_python_source_reports_findings_at_start_and_end(tmp_path, monkeypatch):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))
    lines = []
    for index in range(5_000):
        returned_name = (
            "undefined_at_start"
            if index == 0
            else "undefined_at_end"
            if index == 4_999
            else str(index)
        )
        lines.extend(
            [
                f"def function_{index}():",
                f"    return {returned_name} is {index}",
                f"    # {'x' * 240}",
            ]
        )
    source = "\n".join(lines) + "\n"
    assert len(source.splitlines()) == 15_000
    assert len(source.encode("utf-8")) >= 300_000

    with TestClient(app) as client:
        headers = register(client)
        response = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": source,
                "programGoal": "Return values from the source functions.",
            },
        )

    assert response.status_code == 201
    submission = response.json()
    assert submission["originalCode"] == source
    assert submission["correctedCode"] != source
    assert "return undefined_at_start == 0" in submission["correctedCode"]
    assert "return undefined_at_end == 4999" in submission["correctedCode"]
    assert (
        sum(
            finding["code"] == "LOGIC_IDENTITY_COMPARISON" and finding["fixed"]
            for finding in submission["findings"]
        )
        == 5_000
    )
    undefined_findings = [
        finding for finding in submission["findings"] if finding["code"] == "F821"
    ]
    assert {finding["line"] for finding in undefined_findings} == {2, 14999}


def test_reports_python_syntax_error_and_rejects_unsupported_language(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))

    with TestClient(app) as client:
        headers = register(client)
        invalid_language = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": "print('hi')",
                "programGoal": "Print a greeting.",
                "language": "javascript",
            },
        )
        syntax_error = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": "def broken(:\n    pass\n",
                "programGoal": "Define a function.",
            },
        )
        repairable_syntax_error = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": '# Paste your Python code here\nprint("hello"',
                "programGoal": "Print a greeting.",
            },
        )
        unclosed_string = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": '# Paste your Python code here\nprint("hell',
                "programGoal": "Print a greeting.",
            },
        )
        unclosed_string_before_parenthesis = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": 'print("hello)\n',
                "programGoal": "Print hello.",
                "expectedOutput": "hello\n",
            },
        )
        unclosed_triple_string = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": 'message = """unfinished',
                "programGoal": "Store a message.",
            },
        )
        ambiguous_syntax = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": (
                    "a=intinput(vyvcdcbucq\n"
                    "b=if ygchu=023\n"
                    "import maths\n"
                    "scores = [85,90,78,0\n"
                ),
                "programGoal": "Read values and calculate a score.",
            },
        )
        screenshot_syntax = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": (
                    "# Paste your Python code here\n"
                    'print("hello\n'
                    "a=int(inputenter 0 or 1)\n"
                    "if a:\n"
                    "    print(1)\n"
                ),
                "programGoal": "Read 0 or 1 and print 1 for a nonzero value.",
            },
        )

    assert invalid_language.status_code == 422
    assert syntax_error.status_code == 201
    submission = syntax_error.json()
    assert submission["correctedCode"] == "def broken(:\n    pass\n"
    assert submission["findings"][0]["code"] == "SYNTAX_ERROR"
    assert submission["findings"][1]["code"] == "LOGIC_ANALYSIS_SKIPPED"
    repaired = repairable_syntax_error.json()
    assert repaired["originalCode"] == '# Paste your Python code here\nprint("hello"'
    assert repaired["correctedCode"] == '# Paste your Python code here\nprint("hello")'
    assert repaired["findings"][0]["code"] == "SYNTAX_ERROR"
    assert repaired["findings"][0]["fixed"] is True
    assert '-print("hello"\n+print("hello")\n' in repaired["comparison"]
    unclosed = unclosed_string.json()
    assert unclosed["originalCode"] == '# Paste your Python code here\nprint("hell'
    assert unclosed["correctedCode"] == '# Paste your Python code here\nprint("hell")'
    assert unclosed["findings"][0]["fixed"] is True
    assert '-print("hell\n+print("hell")\n' in unclosed["comparison"]
    repaired_output = unclosed_string_before_parenthesis.json()
    assert repaired_output["correctedCode"] == 'print("hello")\n'
    assert repaired_output["simulation"]["output"] == "hello\n"
    assert repaired_output["simulation"]["testPassed"] is True
    assert '-print("hello)\n+print("hello")\n' in repaired_output["comparison"]
    triple = unclosed_triple_string.json()
    assert triple["correctedCode"] == 'message = """unfinished'
    assert triple["findings"][0]["fixed"] is False
    ambiguous = ambiguous_syntax.json()
    assert ambiguous["originalCode"] == ambiguous["correctedCode"]
    assert ambiguous["findings"][0]["code"] == "SYNTAX_ERROR"
    assert ambiguous["findings"][1]["code"] == "LOGIC_ANALYSIS_SKIPPED"
    repaired_screenshot = screenshot_syntax.json()
    assert repaired_screenshot["correctedCode"] == (
        "# Paste your Python code here\n"
        'print("hello")\n'
        'a=int(input("enter 0 or 1"))\n'
        "if a:\n"
        "    print(1)\n"
    )
    assert len(repaired_screenshot["comparison"]) > 0
    assert '-print("hello\n' in repaired_screenshot["comparison"]
    assert '+print("hello")\n' in repaired_screenshot["comparison"]
    assert len(repaired_screenshot["findings"]) == 2
    assert all(finding["fixed"] for finding in repaired_screenshot["findings"])


def test_program_goal_applies_unverified_best_effort_repairs(tmp_path, monkeypatch):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))

    with TestClient(app) as client:
        headers = register(client)
        missing_goal = client.post(
            "/api/submissions",
            headers=headers,
            json={"code": "print('hello')\n"},
        )
        analysis = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": (
                    "def average(scores):\n"
                    "    if len(scores) is 0:\n"
                    "        return 0\n"
                    "    return sum(scores)\n"
                    "    print('unreachable')\n"
                ),
                "programGoal": "Calculate the average of the scores.",
            },
        )

    assert missing_goal.status_code == 422
    assert analysis.status_code == 201
    submission = analysis.json()
    assert submission["programGoal"] == "Calculate the average of the scores."
    assert submission["originalCode"].endswith("print('unreachable')\n")
    assert "len(scores)" in submission["correctedCode"]
    assert "is 0" not in submission["correctedCode"]
    assert "print('unreachable')" not in submission["correctedCode"]
    findings = {finding["code"] for finding in submission["findings"]}
    assert "LOGIC_IDENTITY_COMPARISON" in findings
    assert "LOGIC_UNREACHABLE_CODE" in findings
    assert "LOGIC_GOAL_AVERAGE_REVIEW" in findings
    identity_finding = next(
        finding
        for finding in submission["findings"]
        if finding["code"] == "LOGIC_IDENTITY_COMPARISON"
    )
    assert identity_finding["fixed"] is True
    assert identity_finding["verified"] is False
    assert all(
        finding["fixed"] and finding["verified"] is False
        for finding in submission["findings"]
        if finding["code"].startswith("LOGIC_")
    )


def test_repairs_common_average_input_errors_when_goal_and_test_confirm(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))

    source = (
        "a = float(input())\n"
        "b = float(input())\n"
        "c = float(input()\n"
        "ag = (a + b + c) / 0\n"
        '\nprint("Average =", avg\n'
    )
    with TestClient(app) as client:
        headers = register(client)
        response = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": source,
                "programGoal": "Calculate the average of 3 numbers.",
                "simulationInput": "1\n2\n3",
                "expectedOutput": "1\n2\n3\nAverage = 2.0",
            },
        )

    assert response.status_code == 201
    submission = response.json()
    assert "c = float(input())" in submission["correctedCode"]
    assert "avg = (a + b + c) / 3" in submission["correctedCode"]
    assert 'print("Average =", avg)' in submission["correctedCode"]
    assert submission["simulation"]["testPassed"] is True
    assert submission["comparison"]
    assert all(finding["fixed"] for finding in submission["findings"])


def test_reanalysis_fixes_multiple_student_record_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))
    source = (
        "students = []\n"
        "def add_student():\n"
        '    name = input("Name: ")\n'
        '    marks = input("Marks: ")\n'
        '    student = {"name": name, "marks": marks}\n'
        "    students.append(student)\n"
        "def update_marks():\n"
        "    return int(input())\n"
        "def display_students():\n"
        "    for student in students:\n"
        '        print(student["mark"])\n'
        "def calculate_average():\n"
        "    total = 0\n"
        "    for student in students:\n"
        '        total = total + student["marks"]\n'
        "    average = total / len(students)\n"
        "    print(average)\n"
        "def search_student(search_name):\n"
        "    for student in students:\n"
        '        if student["name"] = search_name:\n'
        "            return True\n"
        "    return False\n"
    )

    with TestClient(app) as client:
        headers = register(client)
        response = client.post(
            "/api/submissions",
            headers=headers,
            json={"code": source, "programGoal": "Student management system"},
        )
        rescan = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": response.json()["correctedCode"],
                "programGoal": "Student management system",
            },
        )

    assert response.status_code == 201
    submission = response.json()
    corrected = submission["correctedCode"]
    assert 'if student["name"] == search_name:' in corrected
    assert 'marks = int(input("Marks: "))' in corrected
    assert 'print(student["marks"])' in corrected
    assert "if students else 0" in corrected
    assert submission["comparison"]
    assert all(finding["fixed"] for finding in submission["findings"])
    assert rescan.status_code == 201
    assert rescan.json()["correctedCode"] == corrected
    assert rescan.json()["findings"] == []


def test_python_sort_and_extreme_goals_apply_best_effort_fixes(tmp_path, monkeypatch):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))

    with TestClient(app) as client:
        headers = register(client)
        response = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": (
                    "def sorted_values(values):\n"
                    "    return values\n"
                    "def largest_value(values):\n"
                    "    return values[0]\n"
                ),
                "programGoal": "Sort values and return the largest value.",
            },
        )

    assert response.status_code == 201
    submission = response.json()
    assert "return sorted(values)" in submission["correctedCode"]
    assert "return max(values)" in submission["correctedCode"]
    assert "-    return values\n" in submission["comparison"]
    assert any(
        finding["code"] == "LOGIC_GOAL_SORT_REVIEW"
        and finding["fixed"]
        and finding["verified"] is False
        for finding in submission["findings"]
    )
    assert any(
        finding["code"] == "LOGIC_GOAL_EXTREME_REVIEW"
        and finding["fixed"]
        and finding["verified"] is False
        for finding in submission["findings"]
    )


def test_identity_comparison_fix_preserves_unicode_offsets(tmp_path, monkeypatch):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))

    with TestClient(app) as client:
        headers = register(client)
        response = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": (
                    'label_match = "caf\u00e9" is not "empty"\n'
                    "none_match = None is None\n"
                    "print(label_match, none_match)\n"
                ),
                "programGoal": "Compare the value with the empty label.",
                "expectedOutput": "True True\n",
            },
        )

    assert response.status_code == 201
    submission = response.json()
    assert submission["correctedCode"] == (
        'label_match = "caf\u00e9" != "empty"\n'
        "none_match = None is None\n"
        "print(label_match, none_match)\n"
    )
    identity_findings = [
        finding
        for finding in submission["findings"]
        if finding["code"] == "LOGIC_IDENTITY_COMPARISON"
    ]
    assert len(identity_findings) == 1
    assert identity_findings[0]["line"] == 1
    assert identity_findings[0]["fixed"] is True
    assert "matched the supplied expected output" in identity_findings[0]["message"]
    assert submission["simulation"]["testPassed"] is True
    assert submission["simulation"]["output"] == "True True\n"
    assert submission["expectedOutput"] == "True True\n"


@pytest.mark.skipif(
    shutil.which("gcc") is None and shutil.which("clang") is None,
    reason="C analysis requires GCC or Clang",
)
def test_c_source_is_checked_and_stored_without_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))

    with TestClient(app) as client:
        headers = register(client)
        valid = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": "#include <stdio.h>\nint main(void) { return 0; }\n",
                "programGoal": "Return success from a C program.",
                "filename": "hello.c",
                "language": "C",
            },
        )
        comparison = client.patch(
            f"/api/submissions/{valid.json()['id']}",
            headers=headers,
            json={"corrected_code": "int main(void) { return 1; }\n"},
        )
        invalid = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": "int main( { return 0; }\n",
                "programGoal": "Return success from a C program.",
                "filename": "broken.c",
                "language": "c",
            },
        )
        incomplete_printf = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": '// Paste your C code here\nprintf("hell',
                "programGoal": "Print a short greeting.",
                "language": "c",
            },
        )

    assert valid.status_code == 201
    assert valid.json()["language"] == "c"
    assert valid.json()["simulation"]["status"] == "unsupported"
    assert (
        "unless an expected-output test is enabled"
        in valid.json()["simulation"]["message"]
    )
    assert valid.json()["correctedCode"] == valid.json()["originalCode"]
    assert comparison.status_code == 200
    assert "original.c" in comparison.json()["comparison"]
    assert "corrected.c" in comparison.json()["comparison"]
    assert invalid.status_code == 201
    assert any(finding["code"] == "C_ERROR" for finding in invalid.json()["findings"])
    repaired = incomplete_printf.json()
    assert repaired["correctedCode"] == (
        "// Paste your C code here\n\n"
        "#include <stdio.h>\n\n"
        "int main(void) {\n"
        '    printf("hell");\n'
        "    return 0;\n"
        "}\n"
    )
    assert repaired["findings"]
    assert all(finding["fixed"] for finding in repaired["findings"])
    assert '-printf("hell\n' in repaired["comparison"]
    assert '+    printf("hell");\n' in repaired["comparison"]


@pytest.mark.skipif(
    shutil.which("gcc") is None and shutil.which("clang") is None,
    reason="C analysis requires GCC or Clang",
)
def test_c_analysis_applies_iterative_compiler_confirmed_repairs(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))
    source = (
        "#include <stdio.h>\n"
        "int main(void) {\n"
        "    int count = 3\n"
        '    printf("%d\\n", coutn);\n'
        "    return 0;\n"
        "}\n"
    )

    with TestClient(app) as client:
        headers = register(client)
        response = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": source,
                "programGoal": "Print count and return success.",
                "language": "c",
            },
        )
        rescan = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": response.json()["correctedCode"],
                "programGoal": "Print count and return success.",
                "language": "c",
            },
        )

    assert response.status_code == 201
    submission = response.json()
    assert "int count = 3;" in submission["correctedCode"]
    assert 'printf("%d\\n", count);' in submission["correctedCode"]
    assert submission["comparison"]
    assert submission["findings"]
    assert all(finding["fixed"] for finding in submission["findings"])
    assert rescan.status_code == 201
    assert rescan.json()["correctedCode"] == submission["correctedCode"]
    assert rescan.json()["findings"] == []


@pytest.mark.skipif(
    shutil.which("gcc") is None and shutil.which("clang") is None,
    reason="C analysis requires GCC or Clang",
)
def test_large_c_source_is_corrected_and_compared(tmp_path, monkeypatch):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))
    padding = "\n" * 14_997
    source = (
        padding + "double average(int sum, int count) {\n" + "    return sum;\n" + "}\n"
    )
    assert len(source.splitlines()) == 15_000

    with TestClient(app) as client:
        headers = register(client)
        response = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": source,
                "programGoal": "Calculate the average of the values.",
                "filename": "large.c",
                "language": "c",
            },
        )

    assert response.status_code == 201
    submission = response.json()
    assert submission["originalCode"] == source
    assert submission["correctedCode"] != source
    assert "return (double)sum / count;" in submission["correctedCode"]
    average_finding = next(
        finding
        for finding in submission["findings"]
        if finding["code"] == "LOGIC_GOAL_AVERAGE_REVIEW"
    )
    assert average_finding["fixed"]
    assert "best-effort average calculation" in average_finding["message"]
    assert "-    return sum;" in submission["comparison"]
    assert "+    return (double)sum / count;" in submission["comparison"]


@pytest.mark.skipif(
    shutil.which("gcc") is None and shutil.which("clang") is None,
    reason="C analysis requires GCC or Clang",
)
def test_c_expected_output_is_sandboxed_and_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))

    def fake_sandbox(source, simulation_input):
        assert simulation_input == "Ada"
        output = "Goodbye, Ada!\n" if "Goodbye" in source else "Hello, Ada!\n"
        return SimulationResult(
            status="completed",
            output=output,
            message="Mock restricted C sandbox completed.",
        )

    monkeypatch.setattr("app.analyzer.simulate_c_sandbox", fake_sandbox)
    monkeypatch.setattr("app.main.simulate_c_sandbox", fake_sandbox)
    source = (
        "#include <stdio.h>\n"
        "int main(void) {\n"
        "    char name[32];\n"
        '    if (scanf("%31s", name) != 1) return 1;\n'
        '    printf("Hello, %s!\\n", name);\n'
        "    return 0;\n"
        "}\n"
    )
    with TestClient(app) as client:
        headers = register(client)
        created = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": source,
                "programGoal": "Greet the supplied name.",
                "language": "c",
                "simulationInput": "Ada",
                "expectedOutput": "Hello, Ada!",
            },
        )
        loaded = client.get(
            f"/api/submissions/{created.json()['id']}",
            headers=headers,
        )
        updated = client.put(
            f"/api/submissions/{created.json()['id']}",
            headers=headers,
            json={
                "code": source,
                "programGoal": "Greet the supplied name.",
                "language": "c",
                "simulationInput": "Ada",
                "expectedOutput": "Hello, Ada!",
            },
        )
        patched = client.patch(
            f"/api/submissions/{created.json()['id']}",
            headers=headers,
            json={
                "corrected_code": source.replace("Hello", "Goodbye"),
            },
        )

    assert created.status_code == 201
    assert created.json()["simulation"]["testPassed"] is True
    assert created.json()["simulation"]["output"] == "Hello, Ada!\n"
    assert created.json()["simulationInput"] == "Ada"
    assert created.json()["expectedOutput"] == "Hello, Ada!"
    assert loaded.json()["expectedOutput"] == "Hello, Ada!"
    assert updated.status_code == 200
    assert updated.json()["simulation"]["testPassed"] is True
    assert patched.status_code == 200
    assert patched.json()["simulation"]["testPassed"] is False


@pytest.mark.skipif(
    shutil.which("gcc") is None and shutil.which("clang") is None,
    reason="C analysis requires GCC or Clang",
)
def test_c_goal_applies_supported_best_effort_repairs(tmp_path, monkeypatch):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))

    with TestClient(app) as client:
        headers = register(client)
        average = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": ("double average(int sum, int count) {\n    return sum;\n}\n"),
                "programGoal": "Calculate the average.",
                "language": "c",
            },
        )
        sorting = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": "void sort_values(int values[], int n) {\n}\n",
                "programGoal": "Sort the values in ascending order.",
                "language": "c",
            },
        )
        maximum = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": (
                    "int largest_value(int values[], int n) {\n"
                    "    return values[0];\n"
                    "}\n"
                ),
                "programGoal": "Find the largest value.",
                "language": "c",
            },
        )

    assert average.status_code == 201
    assert "return (double)sum / count;" in average.json()["correctedCode"]
    assert average.json()["comparison"]
    assert any(
        finding["code"] == "LOGIC_GOAL_AVERAGE_REVIEW"
        and finding["fixed"]
        and finding["verified"] is False
        for finding in average.json()["findings"]
    )
    assert sorting.status_code == 201
    assert "bh_tmp" in sorting.json()["correctedCode"]
    assert sorting.json()["comparison"]
    assert any(
        finding["code"] == "LOGIC_GOAL_SORT_REVIEW"
        and finding["fixed"]
        and finding["verified"] is False
        for finding in sorting.json()["findings"]
    )
    assert maximum.status_code == 201
    assert "bh_best" in maximum.json()["correctedCode"]
    assert maximum.json()["comparison"]
    assert any(
        finding["code"] == "LOGIC_GOAL_EXTREME_REVIEW"
        and finding["fixed"]
        and finding["verified"] is False
        for finding in maximum.json()["findings"]
    )


@pytest.mark.skipif(
    shutil.which("gcc") is None and shutil.which("clang") is None,
    reason="C analysis requires GCC or Clang",
)
def test_c_repair_is_rejected_when_sandbox_output_mismatches(tmp_path, monkeypatch):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))
    monkeypatch.setattr(
        "app.analyzer.simulate_c_sandbox",
        lambda source, simulation_input: SimulationResult(
            status="completed",
            output="different\n",
            message="Mock restricted C sandbox completed.",
        ),
    )

    source = '// Paste your C code here\nprintf("hello'
    with TestClient(app) as client:
        headers = register(client)
        response = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": source,
                "programGoal": "Print hello.",
                "language": "c",
                "expectedOutput": "hello",
            },
        )

    assert response.status_code == 201
    submission = response.json()
    assert submission["correctedCode"] == source
    assert submission["simulation"]["testPassed"] is False
    assert any(
        finding["code"] == "TEST_OUTPUT_MISMATCH" for finding in submission["findings"]
    )
    assert all(not finding["fixed"] for finding in submission["findings"])


def test_upload_requires_python_or_c_source(tmp_path, monkeypatch):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))

    with TestClient(app) as client:
        headers = register(client)
        rejected = client.post(
            "/api/submissions/upload",
            headers=headers,
            files={"file": ("sample.js", b"console.log('no')", "text/javascript")},
            data={"programGoal": "Print a greeting."},
        )
        missing_goal = client.post(
            "/api/submissions/upload",
            headers=headers,
            files={"file": ("sample.py", b"print('no')", "text/x-python")},
        )
        accepted = client.post(
            "/api/submissions/upload",
            headers=headers,
            files={"file": ("sample.py", b"print('yes')\n", "text/x-python")},
            data={"programGoal": "Print a greeting.", "simulationInput": "9"},
        )

    assert rejected.status_code == 415
    assert missing_goal.status_code == 422
    assert accepted.status_code == 201
    assert accepted.json()["language"] == "python"
    assert accepted.json()["filename"] == "sample.py"
    assert accepted.json()["simulationInput"] == "9"


@pytest.mark.skipif(shutil.which("gcc") is None, reason="GCC is not installed")
def test_upload_accepts_c_file(tmp_path, monkeypatch):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))

    with TestClient(app) as client:
        headers = register(client)
        response = client.post(
            "/api/submissions/upload",
            headers=headers,
            files={"file": ("sample.c", b"int main(void) { return 0; }\n", "text/x-c")},
            data={"programGoal": "Return success from a C program."},
        )

    assert response.status_code == 201
    assert response.json()["language"] == "c"


def test_json_source_is_limited_by_utf8_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))

    with TestClient(app) as client:
        headers = register(client)
        response = client.post(
            "/api/submissions",
            headers=headers,
            json={"code": "é" * 2_500_001, "programGoal": "Store a message."},
        )

    assert response.status_code == 422


def test_submission_crud_keeps_original_and_updates_corrected_code(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))

    with TestClient(app) as client:
        headers = register(client)
        created = client.post(
            "/api/submissions",
            headers=headers,
            json={"code": "print('original')\n", "programGoal": "Print a greeting."},
        )
        submission_id = created.json()["id"]
        updated = client.patch(
            f"/api/submissions/{submission_id}",
            headers=headers,
            json={"corrected_code": "print('edited')\n"},
        )
        listed = client.get("/api/submissions", headers=headers)
        deleted = client.delete(
            f"/api/submissions/{submission_id}",
            headers=headers,
        )
        missing = client.get(
            f"/api/submissions/{submission_id}",
            headers=headers,
        )

    assert created.status_code == 201
    assert updated.json()["originalCode"] == "print('original')\n"
    assert updated.json()["correctedCode"] == "print('edited')\n"
    assert updated.json()["simulation"]["output"] == "edited\n"
    assert len(listed.json()) == 1
    assert deleted.status_code == 204
    assert missing.status_code == 404


def test_replace_submission_reanalyzes_source(tmp_path, monkeypatch):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))

    with TestClient(app) as client:
        headers = register(client)
        created = client.post(
            "/api/submissions",
            headers=headers,
            json={"code": "print('first')\n", "programGoal": "Print a greeting."},
        )
        submission_id = created.json()["id"]
        updated = client.put(
            f"/api/submissions/{submission_id}",
            headers=headers,
            json={
                "code": "import os\nprint('second')\n",
                "programGoal": "Print a second greeting.",
                "language": "python",
            },
        )

    assert updated.status_code == 200
    assert updated.json()["originalCode"] == "import os\nprint('second')\n"
    assert updated.json()["correctedCode"] == "print('second')\n"
    assert any(
        finding["code"] == "F401" and finding["fixed"]
        for finding in updated.json()["findings"]
    )
    assert updated.json()["simulation"]["status"] == "completed"
    assert updated.json()["simulation"]["output"] == "second\n"
    assert updated.json()["programGoal"] == "Print a second greeting."


def test_login_missing_user_requests_registration_and_auth_is_enforced(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))

    with TestClient(app) as client:
        missing = client.post(
            "/api/auth/login",
            json={"email": "new@example.com", "password": "safe-test-password"},
        )
        unauthenticated = client.get("/api/submissions")
        created = client.post(
            "/api/auth/register",
            json={"email": "new@example.com", "password": "safe-test-password"},
        )
        logged_in = client.post(
            "/api/auth/login",
            json={"email": "NEW@example.com", "password": "safe-test-password"},
        )
        invalid_password = client.post(
            "/api/auth/login",
            json={"email": "new@example.com", "password": "incorrect-password"},
        )

    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "registration_required"
    assert missing.json()["detail"]["redirectTo"] == "/register"
    assert unauthenticated.status_code == 401
    assert created.status_code == 201
    assert created.json()["user"]["email"] == "new@example.com"
    assert logged_in.status_code == 200
    assert logged_in.json()["accessToken"]
    assert invalid_password.status_code == 401


def test_frontend_origin_can_call_api_and_camel_case_fields_are_accepted(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))

    with TestClient(app) as client:
        preflight = client.options(
            "/api/submissions",
            headers={
                "Origin": "http://bug-hunter.localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        pages_preflight = client.options(
            "/api/submissions",
            headers={
                "Origin": "https://manojprabhakar6.github.io",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        headers = register(client)
        submission = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": "print('frontend')\n",
                "programGoal": "Print a greeting.",
                "filename": "frontend.py",
                "language": "python",
            },
        )

    assert preflight.status_code == 200
    assert (
        preflight.headers["access-control-allow-origin"]
        == "http://bug-hunter.localhost:5173"
    )
    assert pages_preflight.status_code == 200
    assert (
        pages_preflight.headers["access-control-allow-origin"]
        == "https://manojprabhakar6.github.io"
    )
    assert submission.status_code == 201
    assert submission.json()["originalCode"] == "print('frontend')\n"


def test_logout_revokes_the_access_token(tmp_path, monkeypatch):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))

    with TestClient(app) as client:
        headers = register(client)
        logged_out = client.post("/api/auth/logout", headers=headers)
        after_logout = client.get("/api/auth/me", headers=headers)

    assert logged_out.status_code == 204
    assert after_logout.status_code == 401


def test_c_analysis_requires_an_installed_compiler(tmp_path, monkeypatch):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))
    monkeypatch.setattr("app.analyzer.shutil.which", lambda _: None)

    with TestClient(app) as client:
        headers = register(client)
        response = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": "int main(void) { return 0; }",
                "programGoal": "Return success from a C program.",
                "language": "c",
            },
        )
        submissions = client.get("/api/submissions", headers=headers)

    assert response.status_code == 503
    assert "GCC or Clang" in response.json()["detail"]
    assert submissions.json() == []


def test_demo_sessions_are_temporary_and_isolated(tmp_path, monkeypatch):
    database_path = tmp_path / "demo.sqlite3"
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(database_path))

    with TestClient(app) as client:
        first = client.post("/api/auth/demo")
        second = client.post("/api/auth/demo")
        first_token = first.json()["accessToken"]
        second_token = second.json()["accessToken"]
        first_headers = {"Authorization": f"Bearer {first_token}"}
        second_headers = {"Authorization": f"Bearer {second_token}"}
        first_submissions = client.get("/api/submissions", headers=first_headers)
        second_submissions = client.get("/api/submissions", headers=second_headers)
        cross_account_read = client.get(
            f"/api/submissions/{first_submissions.json()[0]['id']}",
            headers=second_headers,
        )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first_token != second_token
    assert first.json()["user"]["isDemo"] is True
    assert second.json()["user"]["isDemo"] is True
    assert {item["language"] for item in first_submissions.json()} == {"python", "c"}
    assert {
        item["language"]: item["simulation"]["status"]
        for item in first_submissions.json()
    } == {"python": "completed", "c": "unsupported"}
    assert len(second_submissions.json()) == 2
    assert cross_account_read.status_code == 404


def test_expired_demo_token_deletes_its_demo_account(tmp_path, monkeypatch):
    database_path = tmp_path / "demo.sqlite3"
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(database_path))

    with TestClient(app) as client:
        demo = client.post("/api/auth/demo").json()
        token = demo["accessToken"]
        user_id = demo["user"]["id"]
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "UPDATE user_sessions SET expires_at = ? WHERE user_id = ?",
                ("2000-01-01T00:00:00+00:00", user_id),
            )
            connection.execute(
                "UPDATE users SET demo_expires_at = ? WHERE id = ?",
                ("2000-01-01T00:00:00+00:00", user_id),
            )
        expired = client.get(
            "/api/submissions",
            headers={"Authorization": f"Bearer {token}"},
        )

    with sqlite3.connect(database_path) as connection:
        remaining_user = connection.execute(
            "SELECT 1 FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        remaining_submissions = connection.execute(
            "SELECT COUNT(*) FROM submissions WHERE owner_id = ?", (user_id,)
        ).fetchone()[0]

    assert expired.status_code == 401
    assert remaining_user is None
    assert remaining_submissions == 0


def test_accounts_cannot_access_each_others_submissions(tmp_path, monkeypatch):
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(tmp_path / "test.sqlite3"))

    with TestClient(app) as client:
        first_user = register(client, "first@example.com")
        second_user = register(client, "second@example.com")
        created = client.post(
            "/api/submissions",
            headers=first_user,
            json={"code": "print('private')\n", "programGoal": "Print a greeting."},
        )
        other_user_get = client.get(
            f"/api/submissions/{created.json()['id']}",
            headers=second_user,
        )
        other_user_list = client.get("/api/submissions", headers=second_user)

    assert other_user_get.status_code == 404
    assert other_user_list.json() == []


def test_migrates_existing_python_only_database(tmp_path, monkeypatch):
    database_path = tmp_path / "legacy.sqlite3"
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(database_path))
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                language TEXT NOT NULL CHECK (language = 'python'),
                original_code TEXT NOT NULL,
                corrected_code TEXT NOT NULL,
                findings_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO submissions (
                filename, language, original_code, corrected_code,
                findings_json, created_at, updated_at
            ) VALUES ('old.py', 'python', 'print(1)', 'print(1)', '[]', 'old', 'old')
            """
        )

    with TestClient(app) as client:
        headers = register(client)
        c_submission = client.post(
            "/api/submissions",
            headers=headers,
            json={
                "code": "int main(void) { return 0; }\n",
                "programGoal": "Return success from a C program.",
                "language": "c",
            },
        )

    with sqlite3.connect(database_path) as connection:
        legacy_row = connection.execute(
            "SELECT filename, language, expected_output FROM submissions WHERE id = 1"
        ).fetchone()

    assert c_submission.status_code == 201
    assert legacy_row == ("old.py", "python", None)


def test_email_migration_preserves_existing_accounts(tmp_path, monkeypatch):
    database_path = tmp_path / "accounts.sqlite3"
    monkeypatch.setenv("BUG_HUNTER_DATABASE", str(database_path))
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO schema_migrations (version) VALUES (1), (2);
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO users (
                username, password_hash, password_salt, created_at
            ) VALUES (
                'legacy@example.com', 'stored-hash', 'stored-salt', 'created'
            );
            CREATE TABLE user_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            """
        )

    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200

    with sqlite3.connect(database_path) as connection:
        migrated_email = connection.execute(
            "SELECT email FROM users WHERE username = 'legacy@example.com'"
        ).fetchone()[0]

    assert migrated_email == "legacy@example.com"

from app.simulator import simulate_python


def test_preview_uses_supplied_input_and_returns_output():
    result = simulate_python(
        'value = int(input("Number: "))\nprint(value * 2)\n',
        "21",
    )

    assert result.status == "completed"
    assert result.output == "Number: 21\n42\n"


def test_preview_requests_missing_input_and_handles_runtime_errors():
    missing_input = simulate_python('print(input("Name: "))\n')
    runtime_error = simulate_python("print(1 / 0)\n")

    assert missing_input.status == "needs_input"
    assert runtime_error.status == "error"
    assert "division by zero" in runtime_error.message


def test_preview_rejects_unsupported_and_invalid_source():
    unsupported = simulate_python("import os\nprint(os.getcwd())\n")
    invalid = simulate_python("print('unfinished")
    invalid_dictionary = simulate_python("items = {[]: 1}\n")

    assert unsupported.status == "unsupported"
    assert invalid.status == "unsupported"
    assert invalid_dictionary.status == "error"

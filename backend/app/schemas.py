from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_CODE_SIZE = 5_000_000
MAX_PROGRAM_GOAL_LENGTH = 2_000
MAX_SIMULATION_INPUT_LENGTH = 10_000
MAX_EXPECTED_OUTPUT_LENGTH = 20_000
Language = Literal["python", "c"]
SimulationStatus = Literal[
    "completed", "needs_input", "unsupported", "error", "not_run"
]


def to_camel(name: str) -> str:
    first, *rest = name.split("_")
    return first + "".join(part.capitalize() for part in rest)


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


def _validate_source_size(value: str) -> str:
    if len(value.encode("utf-8")) > MAX_CODE_SIZE:
        raise ValueError(f"Source code must not exceed {MAX_CODE_SIZE} UTF-8 bytes.")
    return value


def validate_program_goal(value: str) -> str:
    normalized = value.strip()
    if normalized and len(normalized) < 3:
        raise ValueError(
            "Describe what the program should do in at least 3 characters."
        )
    return normalized


class RegisterInput(ApiModel):
    email: str = Field(
        min_length=6,
        max_length=254,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.lower()


class LoginInput(RegisterInput):
    pass


class UserPublic(ApiModel):
    id: int
    email: str
    created_at: str
    is_demo: bool = False


class AuthToken(ApiModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: str
    user: UserPublic


class SubmissionInput(ApiModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        str_strip_whitespace=False,
    )

    code: str = Field(min_length=1, max_length=MAX_CODE_SIZE)
    program_goal: str = Field(default="", max_length=MAX_PROGRAM_GOAL_LENGTH)
    simulation_input: str = Field(default="", max_length=MAX_SIMULATION_INPUT_LENGTH)
    expected_output: str | None = Field(
        default=None,
        max_length=MAX_EXPECTED_OUTPUT_LENGTH,
    )
    filename: str = Field(default="pasted.py", min_length=1, max_length=255)
    language: Language = "python"

    @field_validator("language", mode="before")
    @classmethod
    def normalize_language(cls, value: str) -> str:
        return value.lower() if isinstance(value, str) else value

    @field_validator("code")
    @classmethod
    def validate_code_size(cls, value: str) -> str:
        return _validate_source_size(value)

    @field_validator("program_goal")
    @classmethod
    def normalize_program_goal(cls, value: str) -> str:
        return validate_program_goal(value)


class CorrectedCodeInput(ApiModel):
    corrected_code: str = Field(min_length=1, max_length=MAX_CODE_SIZE)

    @field_validator("corrected_code")
    @classmethod
    def validate_corrected_code_size(cls, value: str) -> str:
        return _validate_source_size(value)


class Finding(ApiModel):
    code: str
    message: str
    line: int | None = None
    column: int | None = None
    fixed: bool = False
    verified: bool | None = None


class SimulationResult(ApiModel):
    status: SimulationStatus
    output: str = ""
    message: str
    test_passed: bool | None = None


class Submission(ApiModel):
    id: int
    filename: str
    language: Language
    program_goal: str
    simulation_input: str = ""
    expected_output: str | None = None
    simulation: SimulationResult
    original_code: str
    corrected_code: str
    findings: list[Finding]
    comparison: str
    created_at: str
    updated_at: str

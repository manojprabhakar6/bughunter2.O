import ast
import math
import operator
from dataclasses import dataclass, field
from typing import Callable

from app.schemas import SimulationResult

MAX_SIMULATION_STEPS = 10_000
MAX_SIMULATION_ITERATIONS = 1_000
MAX_SIMULATION_OUTPUT = 20_000
MAX_SIMULATION_VALUE_SIZE = 4_096


class _PreviewUnsupported(Exception):
    pass


class _PreviewInputRequired(Exception):
    pass


class _PreviewRuntimeError(Exception):
    pass


class _PreviewBreak(Exception):
    pass


class _PreviewContinue(Exception):
    pass


class _PreviewReturn(Exception):
    def __init__(self, value: object):
        self.value = value


@dataclass(frozen=True)
class _PreviewFunction:
    name: str
    parameters: tuple[str, ...]
    body: list[ast.stmt]


@dataclass
class _PreviewState:
    environment: dict[str, object] = field(default_factory=dict)
    inputs: list[str] = field(default_factory=list)
    output: list[str] = field(default_factory=list)
    steps_remaining: int = MAX_SIMULATION_STEPS
    input_index: int = 0
    call_depth: int = 0

    @property
    def output_size(self) -> int:
        return sum(len(part) for part in self.output)


_BINARY_OPERATORS: dict[type[ast.operator], Callable[[object, object], object]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_AUGMENTED_OPERATORS: dict[type[ast.operator], Callable[[object, object], object]] = {
    **_BINARY_OPERATORS,
}
_COMPARISON_OPERATORS: dict[type[ast.cmpop], Callable[[object, object], bool]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda left, right: operator.contains(right, left),
    ast.NotIn: lambda left, right: not operator.contains(right, left),
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
}


def _tick(state: _PreviewState) -> None:
    state.steps_remaining -= 1
    if state.steps_remaining < 0:
        raise _PreviewUnsupported(
            "The preview step limit was reached; this program was not executed."
        )


def _checked_value(value: object) -> object:
    if value is None or isinstance(value, (bool, str)):
        if isinstance(value, str) and len(value) > MAX_SIMULATION_OUTPUT:
            raise _PreviewUnsupported("A preview value exceeded the size limit.")
        return value
    if isinstance(value, int):
        if value.bit_length() > MAX_SIMULATION_VALUE_SIZE:
            raise _PreviewUnsupported("A preview number exceeded the size limit.")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _PreviewRuntimeError("A calculation produced a non-finite number.")
        return value
    if isinstance(value, range):
        try:
            length = len(value)
        except OverflowError as error:
            raise _PreviewUnsupported(
                "A range exceeds the preview iteration limit."
            ) from error
        if length > MAX_SIMULATION_ITERATIONS:
            raise _PreviewUnsupported("A range exceeds the preview iteration limit.")
        return value
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_SIMULATION_ITERATIONS:
            raise _PreviewUnsupported("A collection exceeded the preview size limit.")
        return type(value)(_checked_value(item) for item in value)
    if isinstance(value, dict):
        if len(value) > MAX_SIMULATION_ITERATIONS:
            raise _PreviewUnsupported("A collection exceeded the preview size limit.")
        return {
            _checked_value(key): _checked_value(item) for key, item in value.items()
        }
    raise _PreviewUnsupported(
        f"Values of type {type(value).__name__} aren't supported in the safe preview."
    )


def _append_output(state: _PreviewState, value: str) -> None:
    if state.output_size + len(value) > MAX_SIMULATION_OUTPUT:
        raise _PreviewUnsupported("The preview output limit was reached.")
    state.output.append(value)


def _safe_arithmetic(
    operation: Callable[[object, object], object],
    left: object,
    right: object,
    node: ast.operator,
) -> object:
    if isinstance(node, ast.Pow) and (
        not isinstance(right, (int, float)) or abs(right) > 100
    ):
        raise _PreviewUnsupported("Exponents above 100 aren't supported.")
    if isinstance(node, ast.Mult):
        if (
            isinstance(left, str | list | tuple)
            and isinstance(right, int)
            and len(left) * abs(right) > MAX_SIMULATION_OUTPUT
        ):
            raise _PreviewUnsupported("A repeated value exceeded the size limit.")
        if (
            isinstance(right, str | list | tuple)
            and isinstance(left, int)
            and len(right) * abs(left) > MAX_SIMULATION_OUTPUT
        ):
            raise _PreviewUnsupported("A repeated value exceeded the size limit.")
        if (
            isinstance(left, int)
            and isinstance(right, int)
            and left.bit_length() + right.bit_length() > MAX_SIMULATION_VALUE_SIZE
        ):
            raise _PreviewUnsupported("A calculation exceeded the number size limit.")
    if (
        isinstance(node, ast.Add)
        and isinstance(left, str | list | tuple)
        and isinstance(right, type(left))
        and len(left) + len(right) > MAX_SIMULATION_OUTPUT
    ):
        raise _PreviewUnsupported("A combined value exceeded the size limit.")

    try:
        return _checked_value(operation(left, right))
    except (ArithmeticError, TypeError, ValueError, OverflowError) as error:
        raise _PreviewRuntimeError(str(error)) from error


def _evaluate(node: ast.expr, state: _PreviewState) -> object:
    _tick(state)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (str, int, float, bool)) or node.value is None:
            return _checked_value(node.value)
        raise _PreviewUnsupported("This constant type isn't supported.")

    if isinstance(node, ast.Name):
        if node.id not in state.environment:
            raise _PreviewRuntimeError(f"name {node.id!r} is not defined")
        return state.environment[node.id]

    if isinstance(node, (ast.List, ast.Tuple)):
        values = [_evaluate(element, state) for element in node.elts]
        return _checked_value(values if isinstance(node, ast.List) else tuple(values))

    if isinstance(node, ast.Dict):
        pairs = zip(node.keys, node.values, strict=True)
        if any(key is None for key in node.keys):
            raise _PreviewUnsupported("Dictionary unpacking isn't supported.")
        try:
            result = {
                _evaluate(key, state): _evaluate(value, state) for key, value in pairs
            }
        except TypeError as error:
            raise _PreviewRuntimeError(str(error)) from error
        return _checked_value(result)

    if isinstance(node, ast.BinOp):
        operator_function = _BINARY_OPERATORS.get(type(node.op))
        if operator_function is None:
            raise _PreviewUnsupported("This arithmetic operator isn't supported.")
        return _safe_arithmetic(
            operator_function,
            _evaluate(node.left, state),
            _evaluate(node.right, state),
            node.op,
        )

    if isinstance(node, ast.UnaryOp):
        operand = _evaluate(node.operand, state)
        if isinstance(node.op, ast.UAdd):
            return _checked_value(+operand)
        if isinstance(node.op, ast.USub):
            return _checked_value(-operand)
        if isinstance(node.op, ast.Not):
            return not operand
        raise _PreviewUnsupported("This unary operator isn't supported.")

    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result: object = True
            for value_node in node.values:
                result = _evaluate(value_node, state)
                if not result:
                    return result
            return result
        if isinstance(node.op, ast.Or):
            result = False
            for value_node in node.values:
                result = _evaluate(value_node, state)
                if result:
                    return result
            return result
        raise _PreviewUnsupported("This boolean operator isn't supported.")

    if isinstance(node, ast.Compare):
        left = _evaluate(node.left, state)
        for operation_node, comparator in zip(node.ops, node.comparators, strict=True):
            operation_function = _COMPARISON_OPERATORS.get(type(operation_node))
            if operation_function is None:
                raise _PreviewUnsupported("This comparison operator isn't supported.")
            right = _evaluate(comparator, state)
            try:
                if not operation_function(left, right):
                    return False
            except (TypeError, ValueError) as error:
                raise _PreviewRuntimeError(str(error)) from error
            left = right
        return True

    if isinstance(node, ast.IfExp):
        branch = node.body if _evaluate(node.test, state) else node.orelse
        return _evaluate(branch, state)

    if isinstance(node, ast.Subscript):
        collection = _evaluate(node.value, state)
        index = _evaluate(node.slice, state)
        if not isinstance(collection, (list, tuple, str, dict)):
            raise _PreviewUnsupported("Indexing this value isn't supported.")
        try:
            return _checked_value(collection[index])
        except (IndexError, KeyError, TypeError) as error:
            raise _PreviewRuntimeError(str(error)) from error

    if isinstance(node, ast.Call):
        return _evaluate_call(node, state)

    raise _PreviewUnsupported(
        f"{type(node).__name__} expressions aren't supported in the safe preview."
    )


def _evaluate_call(node: ast.Call, state: _PreviewState) -> object:
    if not isinstance(node.func, ast.Name):
        raise _PreviewUnsupported("Only approved built-in calls are previewed.")
    name = node.func.id
    user_function = state.environment.get(name)
    if isinstance(user_function, _PreviewFunction):
        if node.keywords:
            raise _PreviewUnsupported("Keyword arguments for user functions aren't supported.")
        arguments = [_evaluate(argument, state) for argument in node.args]
        if len(arguments) != len(user_function.parameters):
            raise _PreviewRuntimeError(
                f"{name}() takes {len(user_function.parameters)} positional "
                f"argument(s) but {len(arguments)} were given"
            )
        if state.call_depth >= 100:
            raise _PreviewUnsupported("The function call-depth limit was reached.")
        outer_environment = state.environment
        state.environment = dict(outer_environment)
        state.environment.update(zip(user_function.parameters, arguments, strict=True))
        state.call_depth += 1
        try:
            _execute_statements(user_function.body, state)
        except _PreviewReturn as returned:
            return _checked_value(returned.value)
        finally:
            state.call_depth -= 1
            state.environment = outer_environment
        return None

    arguments = [_evaluate(argument, state) for argument in node.args]
    keywords = {
        keyword.arg: _evaluate(keyword.value, state) for keyword in node.keywords
    }

    if name == "input":
        if keywords or len(arguments) > 1:
            raise _PreviewUnsupported("This input() form isn't supported.")
        prompt = str(arguments[0]) if arguments else ""
        if state.input_index >= len(state.inputs):
            raise _PreviewInputRequired
        raw_input = state.inputs[state.input_index]
        state.input_index += 1
        _append_output(state, prompt)
        return raw_input

    if name == "print":
        if set(keywords) - {"sep", "end"}:
            raise _PreviewUnsupported("Only print() sep and end options are supported.")
        separator = keywords.get("sep", " ")
        ending = keywords.get("end", "\n")
        if not isinstance(separator, str) or not isinstance(ending, str):
            raise _PreviewRuntimeError("print() sep and end must be strings.")
        _append_output(
            state,
            separator.join(str(argument) for argument in arguments) + ending,
        )
        return None

    if keywords:
        raise _PreviewUnsupported(f"Keyword arguments for {name}() aren't supported.")

    builtins: dict[str, Callable[..., object]] = {
        "int": int,
        "float": float,
        "str": str,
        "bool": bool,
        "len": len,
        "abs": abs,
        "round": round,
        "min": min,
        "max": max,
        "sum": sum,
    }
    if name == "range":
        try:
            result = range(*arguments)
        except (TypeError, ValueError) as error:
            raise _PreviewRuntimeError(str(error)) from error
        return _checked_value(result)
    function = builtins.get(name)
    if function is None:
        raise _PreviewUnsupported(f"Calls to {name}() aren't supported.")
    try:
        return _checked_value(function(*arguments))
    except (ArithmeticError, TypeError, ValueError, OverflowError) as error:
        raise _PreviewRuntimeError(str(error)) from error


def _assign(target: ast.expr, value: object, state: _PreviewState) -> None:
    if isinstance(target, ast.Name):
        state.environment[target.id] = _checked_value(value)
        return
    if isinstance(target, (ast.Tuple, ast.List)):
        if not isinstance(value, (tuple, list)) or len(target.elts) != len(value):
            raise _PreviewRuntimeError("The assigned values don't match the targets.")
        for child_target, child_value in zip(target.elts, value, strict=True):
            _assign(child_target, child_value, state)
        return
    raise _PreviewUnsupported("Only simple variable assignments are previewed.")


def _execute_statement(statement: ast.stmt, state: _PreviewState) -> None:
    _tick(state)

    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if (
            isinstance(statement, ast.AsyncFunctionDef)
            or statement.decorator_list
            or statement.args.defaults
            or statement.args.kw_defaults
            or statement.args.vararg
            or statement.args.kwarg
            or statement.args.kwonlyargs
            or statement.args.posonlyargs
        ):
            raise _PreviewUnsupported(
                "Only simple synchronous functions with positional parameters are previewed."
            )
        state.environment[statement.name] = _PreviewFunction(
            name=statement.name,
            parameters=tuple(argument.arg for argument in statement.args.args),
            body=statement.body,
        )
        return

    if isinstance(statement, ast.Return):
        if state.call_depth == 0:
            raise _PreviewRuntimeError("return outside function")
        value = _evaluate(statement.value, state) if statement.value else None
        raise _PreviewReturn(value)

    if isinstance(statement, ast.Assign):
        value = _evaluate(statement.value, state)
        for target in statement.targets:
            _assign(target, value, state)
        return

    if isinstance(statement, ast.AnnAssign):
        if statement.value is not None:
            _assign(
                statement.target,
                _evaluate(statement.value, state),
                state,
            )
        return

    if isinstance(statement, ast.AugAssign):
        if not isinstance(statement.target, ast.Name):
            raise _PreviewUnsupported("Only simple variable updates are previewed.")
        operation_function = _AUGMENTED_OPERATORS.get(type(statement.op))
        if operation_function is None:
            raise _PreviewUnsupported("This assignment operator isn't supported.")
        if statement.target.id not in state.environment:
            raise _PreviewRuntimeError(f"name {statement.target.id!r} is not defined")
        state.environment[statement.target.id] = _safe_arithmetic(
            operation_function,
            state.environment[statement.target.id],
            _evaluate(statement.value, state),
            statement.op,
        )
        return

    if isinstance(statement, ast.Expr):
        _evaluate(statement.value, state)
        return

    if isinstance(statement, ast.If):
        branch = (
            statement.body if _evaluate(statement.test, state) else statement.orelse
        )
        _execute_statements(branch, state)
        return

    if isinstance(statement, ast.For):
        if statement.orelse and not isinstance(statement.target, ast.Name):
            raise _PreviewUnsupported("This for-loop form isn't supported.")
        if not isinstance(statement.target, ast.Name):
            raise _PreviewUnsupported("Only simple for-loop variables are previewed.")
        iterable = _evaluate(statement.iter, state)
        if not isinstance(iterable, (range, list, tuple, str)):
            raise _PreviewUnsupported("This for-loop iterable isn't supported.")
        if len(iterable) > MAX_SIMULATION_ITERATIONS:
            raise _PreviewUnsupported("The preview iteration limit was reached.")
        completed = True
        for value in iterable:
            _tick(state)
            state.environment[statement.target.id] = value
            try:
                _execute_statements(statement.body, state)
            except _PreviewContinue:
                continue
            except _PreviewBreak:
                completed = False
                break
        if completed:
            _execute_statements(statement.orelse, state)
        return

    if isinstance(statement, ast.Break):
        raise _PreviewBreak

    if isinstance(statement, ast.Continue):
        raise _PreviewContinue

    if isinstance(statement, ast.Pass):
        return

    raise _PreviewUnsupported(
        f"{type(statement).__name__} statements aren't supported in the safe preview."
    )


def _execute_statements(statements: list[ast.stmt], state: _PreviewState) -> None:
    for statement in statements:
        _execute_statement(statement, state)


def simulate_python(
    source: str,
    simulation_input: str = "",
    *,
    syntax_tree: ast.Module | None = None,
) -> SimulationResult:
    if syntax_tree is None:
        try:
            tree = ast.parse(source)
        except (SyntaxError, RecursionError):
            return SimulationResult(
                status="unsupported",
                message="Preview is unavailable until the Python source parses.",
            )
    else:
        tree = syntax_tree

    state = _PreviewState(
        inputs=simulation_input.splitlines(),
    )
    try:
        _execute_statements(tree.body, state)
    except _PreviewInputRequired:
        return SimulationResult(
            status="needs_input",
            output="".join(state.output),
            message="Enter one test value per input() call, then reanalyze.",
        )
    except _PreviewUnsupported as error:
        return SimulationResult(
            status="unsupported",
            output="".join(state.output),
            message=f"Preview unavailable: {error}",
        )
    except (_PreviewRuntimeError, _PreviewBreak, _PreviewContinue) as error:
        return SimulationResult(
            status="error",
            output="".join(state.output),
            message=f"Preview stopped: {error}",
        )
    except RecursionError:
        return SimulationResult(
            status="unsupported",
            output="".join(state.output),
            message="Preview stopped because the source is nested too deeply.",
        )

    return SimulationResult(
        status="completed",
        output="".join(state.output),
        message=(
            "Restricted preview completed; only supported AST operations were "
            "interpreted."
        ),
    )


def verify_expected_output(
    simulation: SimulationResult,
    expected_output: str | None,
) -> SimulationResult:
    if expected_output is None:
        return simulation
    if simulation.status != "completed":
        return simulation.model_copy(
            update={
                "message": (
                    f"{simulation.message} Expected-output test wasn't verified."
                ),
                "test_passed": None,
            }
        )

    actual_output = simulation.output.replace("\r\n", "\n").rstrip("\n")
    normalized_expected = expected_output.replace("\r\n", "\n").rstrip("\n")
    passed = actual_output == normalized_expected
    test_message = (
        "Expected output matched."
        if passed
        else "Output did not match the expected test case."
    )
    return simulation.model_copy(
        update={
            "message": f"{simulation.message} {test_message}",
            "test_passed": passed,
        }
    )

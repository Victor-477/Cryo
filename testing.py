# ============================================================
#  Cryo — the test runner, built in the FRONT END  (roadmap 12.1)
#
#  `test fn name() ={ … }` declares a test. `cryoc test file.cryo` appends a
#  runner to the program that calls each one, reports what happened, and exits
#  non-zero if anything failed.
#
#  WHY IT IS A LOWERING AND NOT A BACKEND FEATURE
#
#  The runner is ordinary Cryo — try/catch, a counter, print, assert — built as
#  AST and appended to the program. No code generator learns anything about
#  tests, so the same suite runs on pyro, go, node and c with no per-backend
#  work and no chance of the six drifting apart. That is architecture rule 2,
#  and it is the same route 11.4's match guards took.
#
#  WHY EACH TEST IS WRAPPED IN try/catch
#
#  `assert` aborts the process. Without isolation the first failing test would
#  end the run and hide every test after it — which is the one thing a test
#  runner must not do, because the failure you can see is rarely the only one.
#  A failing assert raises a catchable value, so each test gets its own handler
#  and the run continues.
# ============================================================
from ast_nodes import (
    Program, FunctionDecl, VarDecl, CallExpr, Literal, Identifier,
    BinaryExpr, TryCatch, CompoundAssignment, Assert,
)


class TestError(Exception):
    pass


# Names the runner introduces. Prefixed so they cannot collide with anything a
# program declares — a test file is still an ordinary program and may well have
# its own `passed`.
_PASS = '__cryo_test_pass'
_FAIL = '__cryo_test_fail'
_ERR = '__cryo_test_err'


def collect(program: Program):
    """Every `test fn` in the program, in source order."""
    return [n for n in program.statements
            if isinstance(n, FunctionDecl) and getattr(n, 'is_test', False)]


def _s(text: str) -> Literal:
    return Literal('string', text)


def build_runner(program: Program) -> Program:
    """Append the runner. Returns the same Program, mutated.

    Order is source order, deliberately: a test that only fails after another
    has run is a real signal, and shuffling would hide it. Cryo has no
    randomness at compile time anyway, so a stable order is also a reproducible
    one.
    """
    tests = collect(program)
    if not tests:
        raise TestError(
            "no tests found: declare one with `test fn name() ={ … }`.\n"
            "  (`test` is only a keyword directly before `fn` — elsewhere it is "
            "an ordinary identifier.)")

    out = [
        VarDecl('int', _PASS, Literal('int', 0)),
        VarDecl('int', _FAIL, Literal('int', 0)),
    ]

    for t in tests:
        out.append(TryCatch(
            try_body=[
                CallExpr(t.name, []),
                CallExpr('print', [_s('  ok   ' + t.name)]),
                CompoundAssignment('+=', _PASS, Literal('int', 1)),
            ],
            catch_type='string',
            catch_name=_ERR,
            catch_body=[
                CallExpr('print', [_s('  FAIL ' + t.name)]),
                # The assert's own message, indented under the test's name —
                # it is the only thing that says WHY, and burying it in the
                # same line as the name makes both harder to read.
                CallExpr('print', [BinaryExpr('+', _s('       '),
                                              Identifier(_ERR))]),
                CompoundAssignment('+=', _FAIL, Literal('int', 1)),
            ],
            finally_body=None,
        ))

    out.append(CallExpr('print', [_s('')]))
    out.append(CallExpr('print', [
        BinaryExpr('+',
            BinaryExpr('+',
                BinaryExpr('+', CallExpr('to_string', [Identifier(_PASS)]),
                           _s(' passed, ')),
                CallExpr('to_string', [Identifier(_FAIL)])),
            _s(' failed'))]))

    # A non-zero exit on failure, without needing an `exit` native the language
    # does not have: a failing assert aborts, and every backend already agrees
    # on what that does.
    #
    # The message is a STRING LITERAL and has to be. `assert(cond, expr)` with
    # anything else silently drops the message and prints "assert failed
    # (line N)" instead — the expression is never even evaluated (roadmap
    # 12.8). The count is on the line above anyway, so nothing is lost here,
    # but a dynamic message would have looked like it worked.
    out.append(Assert(
        BinaryExpr('==', Identifier(_FAIL), Literal('int', 0)),
        _s('the suite has failures — see the report above')))

    program.statements.extend(out)
    return program

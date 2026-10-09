"""AST helpers for unslop_code_scan.py: Python-only precision (string spans, swallowed handlers)."""
import ast, io, re, tokenize, warnings

TRY_NODES = tuple(t for t in (getattr(ast, "Try", None), getattr(ast, "TryStar", None)) if t)
BROAD = {"Exception", "BaseException"}
WRAPPERS = {"wait_for", "run_in_executor", "to_thread", "call_soon_threadsafe", "call_soon", "shield",
            "gather", "run_until_complete", "create_task", "ensure_future", "submit"}
CLEANUP = {"close", "aclose", "stop", "shutdown", "cancel", "cleanup", "terminate", "kill",
           "unlink", "remove", "rmtree", "disconnect", "release", "join"}
# Only exception-related suppressions count; `noqa: E501` or `pylint: disable=line-too-long` do not.
_LINT = re.compile(r"pylint:\s*disable\s*=[^#\n]*(broad-except|broad-exception-caught|bare-except|"
                   r"W0702|W0703|W0718)|noqa:[^#\n]*\b(BLE001|E722|S110)\b", re.I)
_DIRECTIVE = re.compile(r"^\s*(pylint|noqa|type:|fmt:|pragma|isort)", re.I)
MIN_EXCUSE_WORDS = 4   # shortcut: a comment this long demotes a swallow to info, even an AI excuse

def parse(lines):
    """Parse a list of source lines; None when the file is not valid Python."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ast.parse("".join(lines))
    except Exception:
        return None

def string_spans(tree):
    """(line, col, end_line, end_col) of every multi-line string literal (cols are UTF-8 bytes)."""
    out = []
    for n in ast.walk(tree):
        is_str = isinstance(n, ast.JoinedStr) or (isinstance(n, ast.Constant) and isinstance(n.value, str))
        if is_str and n.end_lineno > n.lineno:
            out.append((n.lineno, n.col_offset, n.end_lineno, n.end_col_offset))
    return out

def in_string(spans, lineno, line, char_pos):
    """True when the text at (lineno, char_pos) lies inside a multi-line string literal."""
    col = len(line[:char_pos].encode("utf-8", "ignore"))
    return any((l1, c1) <= (lineno, col) < (l2, c2) for l1, c1, l2, c2 in spans)

def _empty_body(h):
    for s in h.body:
        if isinstance(s, ast.Pass):
            continue
        if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and \
                (s.value.value is Ellipsis or isinstance(s.value.value, str)):
            continue
        return False
    return True

def _name(n):
    return (n.attr if isinstance(n, ast.Attribute) else getattr(n, "id", "")).lower()

def _cleanup_expr(v):
    """A call (maybe awaited) to a cleanup name, or a scheduling wrapper whose callables all are."""
    if isinstance(v, ast.Await):
        v = v.value
    if isinstance(v, ast.Lambda):
        v = v.body
    if isinstance(v, (ast.Attribute, ast.Name)):
        return _name(v) in CLEANUP
    if not isinstance(v, ast.Call):
        return False
    if _name(v.func) in CLEANUP:
        return True
    if _name(v.func) not in WRAPPERS:
        return False
    # positional constants/starred and keywords (timeouts) are ignored; at least one callable must be cleanup
    callables = [a for a in v.args if isinstance(a, (ast.Attribute, ast.Name, ast.Lambda, ast.Call, ast.Await))]
    return bool(callables) and all(_cleanup_expr(a) for a in callables)

def _is_cleanup(stmt):
    if isinstance(stmt, ast.If):
        return all(_is_cleanup(s) for s in stmt.body + stmt.orelse)
    return isinstance(stmt, ast.Expr) and _cleanup_expr(stmt.value)

def comments_by_line(lines):
    """{line_number: comment text} from real COMMENT tokens (a '#' inside a string is not one)."""
    out = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO("".join(lines)).readline):
            if tok.type == tokenize.COMMENT:
                out[tok.start[0]] = tok.string[1:]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return out

def _reason(h, body, comments):
    own = [c for n, c in comments.items() if h.lineno <= n <= h.end_lineno]
    if _LINT.search("\n".join("# " + c for c in own)):
        return "explicit lint suppression for broad except"
    if h.type is None:
        return None
    words = max([len(re.findall(r"[\w'-]+", c)) for c in own if not _DIRECTIVE.match(c)] or [0])
    if words >= MIN_EXCUSE_WORDS:
        return "explained by a comment"
    if body and all(_is_cleanup(s) for s in body):
        return "best-effort cleanup call"
    return None

def swallowed(tree, lines):
    """[(lineno, sev, reason)] for bare/Exception/BaseException handlers that drop the error."""
    out, comments = [], comments_by_line(lines)
    for t in ast.walk(tree):
        if not isinstance(t, TRY_NODES):
            continue
        for h in t.handlers:
            broad = h.type is None or (isinstance(h.type, ast.Name) and h.type.id in BROAD)
            if not broad or not (h.type is None or _empty_body(h)):
                continue
            why = _reason(h, t.body, comments)
            out.append((h.lineno, "info" if why else "medium", why))
    return out

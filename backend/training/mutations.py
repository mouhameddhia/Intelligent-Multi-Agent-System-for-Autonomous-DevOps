"""Single-token mutations of the sample project, used to manufacture failing
builds with a KNOWN root cause (file, function, line, original -> mutated).

Mutating one token in the source text (rather than rewriting the AST) keeps
the diff minimal and the comments intact, so each mutant looks like a small
real-world slip: a wrong operator, an off-by-one, a flipped condition.
"""
import ast
import io
import tokenize
from dataclasses import dataclass
from pathlib import Path

SAMPLE_PROJECT = Path(__file__).resolve().parent / "sample_project"

OPERATOR_SWAPS = {
    "+": ["-"], "-": ["+"], "*": ["/"], "/": ["*"], "//": ["/"], "%": ["//"],
    "<": ["<="], "<=": ["<"], ">": [">="], ">=": [">"], "==": ["!="], "!=": ["=="],
}
NAME_SWAPS = {"and": ["or"], "or": ["and"], "min": ["max"], "max": ["min"]}


@dataclass(frozen=True)
class Mutant:
    file: str        # relative to the project root, e.g. "pricing.py"
    function: str
    line: int
    col: int
    original: str
    mutated: str

    @property
    def id(self) -> str:
        return f"{self.file}:{self.line}:{self.col}:{self.original}->{self.mutated}"

    def apply(self, source: str) -> str:
        lines = source.splitlines(keepends=True)
        text = lines[self.line - 1]
        assert text[self.col:self.col + len(self.original)] == self.original, self.id
        lines[self.line - 1] = text[:self.col] + self.mutated + text[self.col + len(self.original):]
        return "".join(lines)


def _function_at(tree: ast.Module) -> dict[int, str]:
    """Map each source line to the name of the top-level function containing it."""
    owner = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            for line in range(node.lineno, node.end_lineno + 1):
                owner[line] = node.name
    return owner


def _replacements(token: tokenize.TokenInfo) -> list[str]:
    if token.type == tokenize.OP:
        return OPERATOR_SWAPS.get(token.string, [])
    if token.type == tokenize.NAME:
        return NAME_SWAPS.get(token.string, [])
    if token.type == tokenize.NUMBER and token.string.isdigit():
        return [str(int(token.string) + 1)]
    return []


def enumerate_mutants(project: Path = SAMPLE_PROJECT) -> list[Mutant]:
    """Every single-token mutation inside a function body, in a stable order."""
    mutants = []
    for path in sorted(project.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        owner = _function_at(ast.parse(source))
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            (line, col), (end_line, _) = token.start, token.end
            function = owner.get(line)
            if not function or line != end_line:
                continue
            for mutated in _replacements(token):
                mutants.append(Mutant(path.name, function, line, col, token.string, mutated))
    return mutants

"""Enforce the documented internal import boundaries without importing code."""

import argparse
import ast
from pathlib import Path

ALLOWED: dict[str, frozenset[str]] = {
    "vehicle": frozenset(),
    "config": frozenset(),
    "logging": frozenset({"config"}),
    "communication": frozenset({"vehicle", "config", "logging"}),
    "perception": frozenset({"vehicle", "config", "logging"}),
    "safety": frozenset({"vehicle", "config", "logging"}),
    "control": frozenset({"vehicle", "safety", "config", "logging"}),
    "missions": frozenset({"vehicle", "perception", "config", "logging"}),
    "applications": frozenset(
        {
            "vehicle",
            "config",
            "logging",
            "communication",
            "perception",
            "safety",
            "control",
            "missions",
        }
    ),
    "arwen": frozenset(),
}


def modules(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        if node.level:
            return ["<relative>"]
        if node.module == "arwen":
            return [f"arwen.{alias.name}" for alias in node.names]
        return [node.module] if node.module else []
    if isinstance(node, ast.Call) and node.args:
        function = node.func
        name = (
            function.id
            if isinstance(function, ast.Name)
            else (function.attr if isinstance(function, ast.Attribute) else "")
        )
        if name in ("import_module", "__import__") and isinstance(
            node.args[0], ast.Constant
        ):
            value = node.args[0].value
            return [value] if isinstance(value, str) else []
    return []


def check(root: Path) -> list[str]:
    files = sorted(root.rglob("*.py"))
    if not files:
        return [f"No Python modules found under {root}"]
    errors: list[str] = []
    for path in files:
        relative = path.relative_to(root)
        owner = relative.parts[0] if len(relative.parts) > 1 else "arwen"
        if owner not in ALLOWED:
            errors.append(f"{relative}: unknown owning package {owner}")
            continue
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError as exc:
            errors.append(f"{relative}:{exc.lineno}: {exc.msg}")
            continue
        for node in ast.walk(tree):
            for module in modules(node):
                prefix = f"{relative}:{getattr(node, 'lineno', 0)}"
                if module == "<relative>":
                    errors.append(f"{prefix}: use absolute package imports")
                elif module.split(".")[0] == "pymavlink" and owner != "communication":
                    errors.append(f"{prefix}: pymavlink belongs inside communication")
                elif module.startswith("arwen."):
                    target = module.split(".")[1]
                    if target != owner and target not in ALLOWED[owner]:
                        errors.append(f"{prefix}: {owner} must not import {target}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root",
        type=Path,
        nargs="?",
        default=Path(__file__).resolve().parents[1] / "src" / "arwen",
    )
    args = parser.parse_args()
    errors = check(args.root)
    for error in errors:
        print(error)
    if errors:
        return 1
    print("Documented package import boundaries passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

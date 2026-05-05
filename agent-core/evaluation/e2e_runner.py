"""End-to-end scenario runner with JSON report output.

Run: python -m evaluation.e2e_runner
Outputs: evaluation/e2e_report.json
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def run_e2e_tests() -> dict:
    """Run E2E tests via pytest and produce a JSON report."""
    test_file = Path(__file__).parent.parent / "tests" / "test_e2e_scenarios.py"

    result = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            str(test_file),
            "-v",
            "--tb=short",
            "--no-header",
            "-q",
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )

    # Parse pytest output
    lines = result.stdout.strip().split("\n")
    passed = 0
    failed = 0
    errors = 0
    scenarios: list[dict] = []

    for line in lines:
        if "PASSED" in line:
            passed += 1
            name = line.split("::")[1].split(" ")[0] if "::" in line else line
            scenarios.append({"name": name, "status": "passed"})
        elif "FAILED" in line:
            failed += 1
            name = line.split("::")[1].split(" ")[0] if "::" in line else line
            scenarios.append({"name": name, "status": "failed"})
        elif "ERROR" in line:
            errors += 1
            name = line.split("::")[1].split(" ")[0] if "::" in line else line
            scenarios.append({"name": name, "status": "error"})

    total = passed + failed + errors
    completion_rate = (passed / total * 100) if total > 0 else 0.0

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_scenarios": total,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "task_completion_rate": round(completion_rate, 2),
        },
        "scenarios": scenarios,
        "categories": {
            "enrollment": sum(
                1 for s in scenarios
                if "enrollment" in s["name"].lower() or s["name"].startswith("test_0")
            ),
            "scheduling": sum(
                1 for s in scenarios
                if "scheduling" in s["name"].lower() or "schedule" in s["name"].lower()
            ),
            "tutoring": sum(
                1 for s in scenarios
                if "tutoring" in s["name"].lower() or "explain" in s["name"].lower()
            ),
            "planning": sum(
                1 for s in scenarios
                if "plan" in s["name"].lower() or "semester" in s["name"].lower()
            ),
            "safety": sum(
                1 for s in scenarios
                if "safety" in s["name"].lower()
                or "flag" in s["name"].lower()
                or "injection" in s["name"].lower()
            ),
        },
        "exit_code": result.returncode,
        "stdout": result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout,
        "stderr": result.stderr[-1000:] if len(result.stderr) > 1000 else result.stderr,
    }

    return report


def main():
    report = run_e2e_tests()

    output_path = Path(__file__).parent / "e2e_report.json"
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"E2E Report: {output_path}")
    print(f"  Total: {report['summary']['total_scenarios']}")
    print(f"  Passed: {report['summary']['passed']}")
    print(f"  Failed: {report['summary']['failed']}")
    print(f"  Completion Rate: {report['summary']['task_completion_rate']}%")

    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())

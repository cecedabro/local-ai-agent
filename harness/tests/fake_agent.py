"""Stand-in for the coding agent in the self-test. Behaviour keyed on the task id."""
import re
import sys
from pathlib import Path

prompt = sys.argv[-1]
task = re.search(r"## Task (T\d+|CI\d+)", prompt).group(1)
retry = "previous attempt failed" in prompt

if task == "T01":
    Path("hello.txt").write_text("hello\n")
elif task == "T02":
    Path("tests").mkdir(exist_ok=True)
    Path("tests/test_cheat.py").write_text("assert True\n")   # protected: must be rejected
elif task == "T03":
    Path("out.txt").write_text("right\n" if retry else "wrong\n")
elif task == "T04":
    Path("ci").mkdir(exist_ok=True)
    Path("ci/check.sh").write_text("exit 0\n")               # protected by .agent-protected
elif task.startswith("CI") and "ci says: ci-fixed.txt is missing" in prompt:
    Path("ci-fixed.txt").write_text("fixed\n")               # only if the CI log reached the prompt
with Path("PROGRESS.md").open("a") as fh:
    fh.write(f"- {task}: fake agent ran (retry={retry})\n")
print(f"fake agent did {task}")

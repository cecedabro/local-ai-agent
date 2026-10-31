You are working in a git repository, alone, without a human to ask. Do exactly one task.

## Task {{task_id}}
{{task}}

## How you will be judged
After you stop, the harness runs these commands. The task counts as done only if all exit 0:
- `{{accept}}`
- `{{check}}`

## Rules
- Read `AGENTS.md` first for stack, commands and conventions. Read the parts of `SPEC.md` this task needs.
- Do not modify these paths; changes there reject the attempt: {{protected}}.
  If a test seems wrong, make the code satisfy it anyway and note the concern in PROGRESS.md.
- Keep the change small and focused on this task. Don't start other tasks.
- Run the commands above yourself before you stop, and fix what fails.
- Don't commit; the harness commits.
- When done, append 3–5 lines to `PROGRESS.md`: what you changed, where, and anything a later task must know.

## Recent progress (from PROGRESS.md)
{{progress}}

{{failure}}

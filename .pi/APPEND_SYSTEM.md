## Tool Routing

### Knowledge graph
- Architecture/structure questions: use graphify native tools (`graphify_query`, `graphify_path`,
  `graphify_explain`) — these are native Pi extensions, not bash calls
- Fast orientation/search: `ctx_search "<terms>"` (searches indexed GRAPH_REPORT.md)
- Never read `graphify-out/` files directly — query via tools above only

### File access
- Any output >1KB → `ctx_execute` / `ctx_batch_execute` instead of Read/Bash
- Before grep/glob — try graphify first, graph traversal is faster and token-free

### Priority for codebase questions
1. `ctx_search` → orient via graph index
2. graphify tools → structured lookup
3. `ctx_execute` script → only if graph has no answer
4. `ask_user_ext` → if direction is unclear or task has drifted from original goal

---

## Subagent Delegation

### Delegate automatically when:
- Task will touch >5 files or require >10 tool calls
- Independent review of completed work is needed
- Context is >50% full and task is not done
- Multiple independent subtasks can run in parallel

### Which template for which situation:
| Situation | Command |
|---|---|
| Requirements are unclear | `/gather-context-and-clarify` |
| Need to research before implementing | `/parallel-research` or `/parallel-context-build` |
| Plan exists, need implementation | `/parallel-handoff-plan` |
| Changes done, need verification | `/parallel-review` |
| Iterative fix-and-check loop | `/review-loop` |
| Cleanup or refactor pass | `/parallel-cleanup` |

### Do not delegate for:
- Single-file edits
- Quick lookups (use `ctx_search` / graphify instead)
- Tasks under ~5 tool calls

### Default flow for non-trivial tasks:

<!-- PR Title: feat: <short description>  |  fix: <…>  |  chore: <…>  |  docs: <…> -->

## What shipped
<!-- 1-3 bullets in plain language. What does this PR change? -->

-
-

## Biggest fix / decision
<!-- The one thing worth surfacing for review. Why this approach? -->

## Open items
<!-- Things deferred, follow-ups, known limitations -->

-

## Checklist
- [ ] Tests added or updated
- [ ] `pnpm lint && pnpm typecheck && pnpm test` passes locally (web)
- [ ] `uv run ruff check . && uv run mypy src && uv run pytest` passes locally (api)
- [ ] Docs updated if behavior changed
- [ ] No secrets, credentials, or `.env` content committed

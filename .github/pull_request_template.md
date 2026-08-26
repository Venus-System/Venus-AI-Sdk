<!--
  Fill in the sections below. Delete anything that does not apply.
  PR title: follow the same convention as the commits, for example:
  feat: add product page
  fix: correct keyboard focus in modal
-->

## What this PR does

<!-- One or two sentences explaining the change, in plain language. -->

## Type of change

- [ ] `feat` new feature
- [ ] `fix` bug fix
- [ ] `refactor` internal change, no behavior difference
- [ ] `docs` documentation

## Evidence

<!-- Screenshot, screen recording, or link to the deployed environment.
     Required whenever the change affects something visible. -->

## Author checklist

- [ ] The title follows the conventional commits convention
- [ ] I manually tested what changed and described how to reproduce it above
- [ ] I left no `console.log` or commented-out code
- [ ] I did not commit any key, password, or private URL
- [ ] All new code in `src/` is `.ts` or `.tsx` and free of `any` types.
- [ ] API calls are isolated in `src/services/` (no direct fetch in components).
- [ ] `useEffect` dependency arrays are explicit and correct.
- [ ] Dynamic lists use unique data identifiers as `key` (no `key={index}`).
- [ ] Async operations have visual UI feedback (loading, success, error).
- [ ] Basic accessibility is met (semantic `<button>`, `<label>`, image `alt`, and keyboard navigation works).
- [ ] I updated the documentation, if needed

## Notes for the reviewer

<!-- Anything that deserves special attention, an open question,
     or a decision you would like to discuss. -->
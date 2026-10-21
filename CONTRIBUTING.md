# Contributing

Thanks for your interest in this repository. This project is the portfolio
proof-of-work for the Datheon Python Programming Internship (Credential ID
`DT-2026-O6Q0X`), maintained by Krish Sharma.

## Branching model

`main` is protected: **never commit or push directly to it.** All work travels
through a short-lived branch and a reviewed pull request.

| Purpose | Branch name pattern |
| --- | --- |
| Daily feature work | `feat/datheon-day-<N>-<topic>` |
| Refactors | `refactor/internship-<task>` |
| Bug fixes | `fix/<short-description>` |
| Documentation | `docs/<short-description>` |

## Workflow

1. **Branch** off an up-to-date `main`:

   ```bash
   git checkout main && git pull
   git checkout -b feat/datheon-day-1-scaffold
   ```

2. **Make a focused change.** One logical improvement per branch. Keep the diff
   small and reviewable.

3. **Meet the code standard.**
   - Type hints on all public callables.
   - Google-style docstrings on every public module, class, and function.
   - No new runtime dependencies without discussion — the projects are
     standard-library only.
   - Add or update tests for any behaviour change.

4. **Verify locally before pushing.**

   ```bash
   python -m py_compile $(git ls-files '*.py')   # syntax check
   pytest -q                                     # tests must pass
   flake8 .                                      # lint clean
   black --check .                               # formatting clean
   ```

5. **Commit** using [Conventional Commits](https://www.conventionalcommits.org/):

   ```text
   feat(hangman): add category-based word banks
   fix(organizer): handle permission errors on move
   docs(readme): document quickstart
   ```

6. **Open a Pull Request** targeting `main`. Describe what changed and why, and
   confirm the CI checks are green.

7. **Review → Squash merge → delete the branch.**

## Commit message convention

```text
<type>(<scope>): <short summary>

[optional body]
```

Accepted types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`.

## Definition of done

- [ ] Syntax check passes (`py_compile`).
- [ ] `pytest` is green.
- [ ] `flake8` and `black --check` are clean.
- [ ] Docstrings and type hints are present.
- [ ] No secrets, tokens, or credentials are committed.
- [ ] The PR has been reviewed and squash-merged.

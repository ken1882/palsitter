# Palsitter Documentation

Specifications are organized first by ownership, then by concern:

- [`shared/`](./shared/README.md) — application chrome, multi-game workflows, visual
  conventions, storage/runtime conventions, localization, and reusable UI features.
- [`games/palworld/`](./games/palworld/README.md) — the complete Palworld runtime and
  instance UI contract.
- [`games/satisfactory/`](./games/satisfactory/README.md) — the intentionally limited
  Satisfactory placeholder contract.

Within each scope, **components** describe a page or visible layout and **features**
describe behavior spanning components or backend services. Components link to the
features that implement their behavior instead of repeating those rules.

## Placement Rules

- Put a document in `shared` only when the behavior belongs to the application shell or
  is intentionally reusable by multiple games.
- Put game-specific pages, fields, paths, ports, processes, APIs, backups, and lifecycle
  behavior under that game's directory.
- When adding a game, create `docs/games/<game>/README.md` and add component/feature files
  only for capabilities that game actually exposes. Do not copy Palworld specs as defaults.
- Every testable GUI behavior requires a Playwright test using the real UI path; backend
  behavior additionally requires focused unit tests with external dependencies faked.

## Index

- [Shared specifications](./shared/README.md)
- [Palworld specifications](./games/palworld/README.md)
- [Satisfactory specifications](./games/satisfactory/README.md)

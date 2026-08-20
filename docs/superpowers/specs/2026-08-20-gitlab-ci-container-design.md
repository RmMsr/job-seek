# GitLab CI pipeline for container build & push

## Goal

Add a `.gitlab-ci.yml` that builds the app's container image (from the existing
`Containerfile`) and pushes it to the GitLab Container Registry, gated behind
the test suite passing.

## Stages

`test` → `build`

### `test` job

Runs on every pipeline (every push, every branch/MR):

```yaml
test:
  stage: test
  image: ghcr.io/astral-sh/uv:python3.12-trixie-slim
  script:
    - uv sync --frozen
    - uv run pytest
```

### `build` job

Runs only on pushes to the default branch (`main`), after `test` passes:

```yaml
build:
  stage: build
  rules:
    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'
  image: quay.io/buildah/stable
  before_script:
    - echo "$CI_REGISTRY_PASSWORD" | buildah login -u "$CI_REGISTRY_USER" --password-stdin "$CI_REGISTRY"
  script:
    - buildah build --layers --file Containerfile --tag "$CI_REGISTRY_IMAGE:$CI_COMMIT_SHORT_SHA" --tag "$CI_REGISTRY_IMAGE:latest" .
    - buildah push "$CI_REGISTRY_IMAGE:$CI_COMMIT_SHORT_SHA"
    - buildah push "$CI_REGISTRY_IMAGE:latest"
```

`$CI_REGISTRY_USER` / `$CI_REGISTRY_PASSWORD` / `$CI_REGISTRY` / `$CI_REGISTRY_IMAGE`
are provided automatically by GitLab for every pipeline — no project CI/CD
variables need to be configured.

## Key decisions

- **Registry:** GitLab Container Registry (built into the project), not an
  external registry — zero extra credentials to manage.
- **Trigger:** build only on `main` pushes. No build-on-tag / release flow —
  this is a personal app with continuous deployment from `main`, not a
  versioned release process.
- **Test gate:** `pytest` must pass before `build` runs (separate stage,
  default stage-sequencing dependency — no image gets built from broken code).
- **Builder: buildah**, not Docker+dind. The `Containerfile` uses BuildKit
  syntax (`# syntax=docker/dockerfile:1`, `RUN --mount=type=cache`). Buildah
  has supported `RUN --mount=type=cache` since v1.29 (verified against
  `quay.io/buildah/stable`'s current release notes), so the Containerfile
  needs no changes. Buildah also runs rootless — no privileged GitLab Runner
  required, unlike a dind-based Docker build.
- **Tags pushed:** `$CI_COMMIT_SHORT_SHA` (traceability — always know exactly
  which commit an image came from) and `latest` (what `docker/podman pull`
  without a tag gets, matching the README's existing pull instructions).

## Out of scope (explicitly not doing)

- Cross-run layer caching. GitLab shared runners are ephemeral per job, so
  buildah's `--mount=type=cache` only helps within one job's own multi-stage
  build, not across separate pipeline runs. A registry-based build cache is a
  reasonable future improvement but adds complexity not needed now.
- Multi-arch builds (`linux/amd64` only, matching current manual build
  instructions in the README).
- Container registry cleanup/expiration policy.
- Tag-triggered release builds — this is a `main`-is-always-deployable app,
  not a versioned-release app (see relagent's CI, which does use tag-gated
  release jobs, for a project that needs that instead).

## Validation plan

- Lint `.gitlab-ci.yml` as valid YAML locally.
- Sanity-build the `Containerfile` locally with `podman build` (buildah
  itself isn't installed in this dev environment, but podman shares its
  build engine and the README already documents `podman build` against this
  same Containerfile) to confirm the file itself still builds cleanly.
- Push and observe the real pipeline run on GitLab
  (`gitlab.com:RmMsr/job-seek.git` — a real remote already exists for this
  repo, so the pipeline will actually execute once pushed).

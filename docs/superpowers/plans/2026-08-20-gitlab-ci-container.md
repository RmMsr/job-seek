# GitLab CI Container Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `.gitlab-ci.yml` that runs the test suite on every pipeline and, only on pushes to `main`, builds the existing `Containerfile` with buildah and pushes it to the GitLab Container Registry.

**Architecture:** Two-stage pipeline (`test` → `build`). The `test` job runs `pytest` in a `uv`-based image. The `build` job runs only when `$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH`, uses the rootless `quay.io/buildah/stable` image to build `Containerfile` and push two tags (`$CI_COMMIT_SHORT_SHA` and `latest`) to `$CI_REGISTRY_IMAGE`, authenticating with GitLab's auto-provided `$CI_REGISTRY_USER`/`$CI_REGISTRY_PASSWORD`.

**Tech Stack:** GitLab CI/CD, buildah (`quay.io/buildah/stable`), `uv` (`ghcr.io/astral-sh/uv:python3.12-trixie-slim`), the repo's existing `Containerfile`.

## Global Constraints

- Registry: GitLab Container Registry only (`$CI_REGISTRY_IMAGE`) — no external registry, no new CI/CD variables to configure.
- Build trigger: pushes to `main` only — no tag-triggered release flow.
- The `build` job must not run unless `test` has passed.
- Do not modify `Containerfile` — its BuildKit cache-mount syntax (`RUN --mount=type=cache`) is already supported by buildah v1.29+, which `quay.io/buildah/stable` satisfies.
- Tags pushed: `$CI_COMMIT_SHORT_SHA` and `latest`.

---

### Task 1: Add `.gitlab-ci.yml`

**Files:**
- Create: `.gitlab-ci.yml`

**Interfaces:**
- N/A — this is a standalone CI config file, no code interfaces to other tasks.

- [ ] **Step 1: Write `.gitlab-ci.yml`**

```yaml
stages:
  - test
  - build

test:
  stage: test
  image: ghcr.io/astral-sh/uv:python3.12-trixie-slim
  script:
    - uv sync --frozen
    - uv run pytest

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

- [ ] **Step 2: Validate the YAML parses**

Run: `python3 -c "import yaml; yaml.safe_load(open('.gitlab-ci.yml'))" && echo OK`
Expected: `OK` printed, no exception.

- [ ] **Step 3: Sanity-build the referenced `Containerfile` locally**

Buildah itself isn't installed in this dev environment, but `podman build` shares the same build engine and already builds this exact `Containerfile` per the README — this confirms the file the pipeline will build is not broken, independent of GitLab-specific behavior.

Run: `podman build --file Containerfile --tag job-seek-ci-sanity-check .`
Expected: build completes successfully (exit code 0). This will take a few minutes (installs Playwright + Chromium).

Clean up the sanity image afterward: `podman rmi job-seek-ci-sanity-check`

- [ ] **Step 4: Commit**

```bash
git add .gitlab-ci.yml
git commit -m "ci: add GitLab CI pipeline to test and build the container image"
```

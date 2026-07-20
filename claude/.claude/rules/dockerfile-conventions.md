---
paths:
  - "**/Dockerfile"
  - "**/Dockerfile.*"
  - "**/*.Dockerfile"
  - "**/*.dockerfile"
---

## Dockerfile conventions

Sources verified against Docker's official docs (2026-07): "Best practices for
building images" and the BuildKit "Build secrets" guide.

- **Pin the base image by digest, not a floating tag.** Docker: "Image tags
  are mutable, meaning a publisher can update a tag to point to a new image."
  Pinning to a digest (`FROM alpine:3.21@sha256:...`) guarantees "you're
  guaranteed to always use the same image version, even if a publisher
  replaces the tag." Tradeoff: a pinned digest freezes out upstream security
  rebuilds silently — pair with an active refresh mechanism (e.g. Renovate/
  Dependabot digest updates), don't pin and forget.
- **Run as a non-root `USER`.** Docker: "If a service can run without
  privileges, use `USER` to change to a non-root user," and avoid installing
  `sudo` ("unpredictable TTY and signal-forwarding behavior").
- **Use multi-stage builds.** Docker: multi-stage builds "reduce the size of
  your final image, by creating a cleaner separation between the building of
  your image and the final output" — ship only runtime artifacts, not build
  toolchains or intermediate source.
- **Never bake secrets into layers via `ARG`/`ENV`/`COPY`.** Docker: "Build
  arguments and environment variables are inappropriate for passing secrets to
  your build, because they persist in the final image." Use
  `RUN --mount=type=secret,id=<name> ...` instead — the secret is mounted only
  for that instruction and never written to the image or its history.
- **`COPY` explicit paths, not `COPY . .`** — the primary control against
  baking `.env`/keys/`.git` into a layer.
- **Add a `.dockerignore`** (`.git`, `node_modules`, `.env*`, build output) as
  defense-in-depth alongside explicit `COPY` paths — Docker: "To exclude files
  not relevant to the build... use a `.dockerignore` file," but a forgotten
  entry doesn't fail loudly, so don't rely on it alone.
- **`apt-get update` and `apt-get install` in the SAME `RUN` layer**, with
  `--no-install-recommends` and cleaning the package list. Docker: "Always
  combine `RUN apt-get update` with `apt-get install` in the same `RUN`
  statement" — "Using `apt-get update` alone... causes caching issues and
  subsequent `apt-get install` instructions to fail" (a separate `update` layer
  caches and serves a stale index on rebuild).

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

- **Pin the base image by digest, not a floating tag** — tags are mutable
  (Docker docs) — and pair the pin with an automated digest-refresh (e.g.
  Renovate/Dependabot) since a frozen digest silently misses upstream
  security rebuilds.
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
- **Combine `apt-get update` and `apt-get install` in the same `RUN` layer**
  (with `--no-install-recommends` and cache cleanup) — a separate `update`
  layer caches a stale index that later `apt-get install` calls silently
  reuse.

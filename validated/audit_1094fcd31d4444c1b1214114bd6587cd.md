### Title
`Archive` invoked with `context.Background()` instead of the caller's cancellable `ctx`, defeating job-cancellation propagation for artifact archiving - ([File: commands/helpers/artifacts_uploader.go])

### Summary
In `createBodyProvider`, the goroutine that drives the archiver calls `archiver.Archive(context.Background(), c.files)` instead of using the request/job context that the `StreamProvider.ReaderFactory` closure could receive, so cancellation of the outer context can never reach `archiver.Archive` (and thus `fastzip.Archiver.Archive`) through this call site. [1](#0-0) 

### Finding Description
`createBodyProvider` builds a `StreamProvider` whose `ReaderFactory` starts a goroutine that runs the archiver and writes into a pipe:

```go
go func() {
    archiveErr := archiver.Archive(context.Background(), c.files)
    pw.CloseWithError(archiveErr)
}()
``` [1](#0-0) 

`context.Background()` is never canceled, so even if the job/pipeline delivers a cancellation signal through some outer `ctx` (e.g. from `functions/concrete/run/stages/artifact_upload.go`'s `Run(ctx context.Context, ...)` which passes `ctx` to `e.RunnerCommand`), that cancellation is not wired into the goroutine that actually drives `fastzip.Archiver.Archive` for the archiving portion of the work — it is disconnected from the archive step entirely. This is independent of whether `fastzip`'s internal worker pool checks `ctx.Err()` per file (as raised in the question), because the `ctx` passed into `fa.Archive` inside `commands/helpers/archive/fastzip/zip_fastzip_archiver.go`'s `Archive` at line 106 is `context.Background()`, not a cancellable context tied to job state: [2](#0-1) 

However, this alone does not establish resource-tying-up impact past job cancellation for the artifacts-uploader **process**, because the artifacts-uploader is itself invoked as a separate helper subprocess by `RunnerCommand` (`functions/concrete/run/stages/artifact_upload.go` line 118) using the stage's `ctx`. I was not able to confirm from available code whether `RunnerCommand`'s implementation uses `exec.CommandContext` (which would send a kill signal to the whole subprocess on cancellation, terminating the in-process goroutine and `fastzip` worker pool regardless of internal `ctx.Err()` checks) or a plain `exec.Command` with separate signal/timeout handling. I located `RunnerCommand` only by name reference in `functions/concrete/run/env/env.go` and `shells/abstract.go`, but did not get to inspect its body before running out of iterations.

### Impact Explanation
If the outer process-kill mechanism (via `RunnerCommand`/`exec.CommandContext` or equivalent) reliably terminates the artifacts-uploader helper process on job cancellation, then the disconnected `context.Background()` inside `Archive` is a latent/dead code smell rather than an exploitable persistent-disruption bug, because process termination bounds the goroutine's lifetime regardless of internal `ctx` plumbing. If instead the outer mechanism relies on cooperative `ctx.Err()` checking within the same process (no forced kill), then this disconnect means a job with many/large files can keep the archiving goroutine (and therefore `fastzip`'s CPU/memory-bound worker pool) running to completion after cancellation, consuming host-shared runner resources — this matches the scoped impact. I could not confirm which of these two scenarios holds within the exploration budget available.

### Likelihood Explanation
Preconditions match the question exactly: an attacker only needs to control the number/size of artifact files (via `artifacts:paths` in `.gitlab-ci.yml`), which is a standard, unprivileged CI author capability. If the outer process is not force-killed, the exploit path is fully reachable: `artifacts-uploader` --> `createBodyProvider` --> goroutine with `context.Background()` --> `archiver.Archive` --> `fastzip.Archiver.Archive`, with no `ctx` cancellation check anywhere on this path.

### Recommendation
1. Thread the real, cancellable context through `createBodyProvider`'s `ReaderFactory` and use it in the `archiver.Archive(ctx, c.files)` call instead of `context.Background()`, so cancellation of the job/upload timeout is honored even by cooperative (non-killed) code paths.
2. Independently confirm (in a follow-up session with full repo/tool access) whether `RunnerCommand` uses `exec.CommandContext` or another hard-kill mechanism for the `artifacts-uploader` subprocess; if it does not, add one, since that is the actual backstop against runaway archiving regardless of `fastzip`'s internal cooperative-cancellation support.
3. Consider passing a bounded-concurrency, cancellation-aware wrapper around `fastzip.Archiver.Archive` (e.g., running it in a goroutine and select-ing on `ctx.Done()` to return early with `ctx.Err()`, even if the underlying worker pool keeps running detached) so the helper process's own logic gives up its result promptly.

### Proof of Concept
Go unit test in `commands/helpers/artifacts_uploader_test.go` or a new test file in `commands/helpers/archive/fastzip`:
1. Create a `StreamProvider` (or call `createBodyProvider` directly with an injected `context.Context` that is canceled after a short delay) with a `c.files` map containing thousands of synthetic file entries backed by a slow/blocking `os.FileInfo`/file-read shim.
2. Assert that after canceling the context passed conceptually to the archive step, the goroutine driving `archiver.Archive` returns (pipe closes) within a bounded time and that `archiveErr` is `context.Canceled` — this will currently fail because `Archive` is called with `context.Background()`, so the archiving continues to completion regardless of the outer cancellation.
3. Separately, an integration-level test should verify whether the actual `gitlab-runner artifacts-uploader` subprocess is killed by the parent when the stage `ctx` is canceled (inspecting `RunnerCommand`'s implementation directly, which was not available in this session), to determine whether the in-process context bug is exploitable in practice or masked by process-level termination.

### Citations

**File:** commands/helpers/artifacts_uploader.go (L122-126)
```go
			// Start a new Goroutine to create the archive for this attempt
			go func() {
				archiveErr := archiver.Archive(context.Background(), c.files)
				pw.CloseWithError(archiveErr)
			}()
```

**File:** commands/helpers/archive/fastzip/zip_fastzip_archiver.go (L106-106)
```go
	err = fa.Archive(ctx, files)
```

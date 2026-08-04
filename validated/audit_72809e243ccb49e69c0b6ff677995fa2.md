### Title
`GetSources.Run`'s retry loop calls `retry.SleepWithNotice` with a bare `time.Sleep`, ignoring job cancellation for up to 5 minutes per attempt - ([File: functions/concrete/run/stages/internal/retry/retry.go])

### Summary
`retry.SleepWithNotice` performs `time.Sleep(d)` with no `ctx.Done()` select, and `GetSources.Run` calls it inside a loop bounded by `s.MaxAttempts` when `UseExponentialBackoffStageRetry` is set, with backoff durations up to 5 minutes (`NewBackoff`'s `Max: 5 * time.Minute`). Since the sleep is not context-aware, canceling the job's context mid-retry does not interrupt the sleep, so the executor goroutine running `GetSources.Run` remains blocked for up to 5 minutes after cancellation.

### Finding Description
`SleepWithNotice` at `functions/concrete/run/stages/internal/retry/retry.go:21-24` is: [1](#0-0) 
It takes no `context.Context` argument at all and simply calls `time.Sleep(d)`, which cannot be interrupted by cancellation.

`GetSources.Run` at `functions/concrete/run/stages/get_sources.go:148-167` builds a backoff schedule with `retry.NewBackoff()` (5s min, 5 min max, exponential factor 1.5) and loops `attempt := 1; attempt <= s.MaxAttempts`, calling `retry.SleepWithNotice(e, backoff.Duration())` before each retry when `s.UseExponentialBackoffStageRetry` is true: [2](#0-1) 

Even though `ctx` is threaded through `getSourcesOnce` and the underlying git commands (so an in-flight git process can be canceled), the sleep between attempts is not gated on `ctx.Done()`. If the job's context is canceled while the loop is inside `SleepWithNotice`, execution does not return until the full sleep duration elapses — up to 5 minutes per retry, and this can repeat for each subsequent attempt up to `MaxAttempts`.

`MaxAttempts` and `UseExponentialBackoffStageRetry` are fields on the `GetSources` struct populated from job-controlled configuration (`GET_SOURCES_ATTEMPTS` and related CI/CD variables map into this struct via the builder). A pipeline author can set `GET_SOURCES_ATTEMPTS` to a large value to increase the number of retry iterations the loop can reach, and combined with `UseExponentialBackoffStageRetry=true`, each iteration boundary is an opportunity for the uninterruptible sleep to occur.

No existing check bounds the sleep by context: `getSourcesOnce` calls are context-aware, but the inter-attempt sleep is not. This is a genuine gap between the job-cancellation invariant (which should promptly release resources) and the implementation.

### Impact Explanation
When a job is canceled while `GetSources.Run`'s retry loop is sleeping, the executor worker goroutine handling that job's `get_sources` stage stays blocked up to 5 minutes past the cancellation signal (the `NewBackoff` max), and this can recur on each subsequent retry attempt if `err` continues to occur, until `MaxAttempts` is exhausted or the sleeps run out. On runners with a limited/serialized number of concurrent job slots, this ties up a worker slot longer than intended after cancellation, delaying or starving other queued jobs (potentially from other projects) waiting on that runner. This matches the scoped impact: a multi-tenant disruption where cancellation does not promptly free the runner slot.

### Likelihood Explanation
Preconditions are attacker-controllable and low-privilege: a pipeline author sets `GET_SOURCES_ATTEMPTS` (mapped to `MaxAttempts`) to a value >1, and needs `UseExponentialBackoffStageRetry` enabled (a runner/job-level feature toggle — whether this is user-settable or admin/feature-flag-gated could not be fully confirmed from the code reachable in this session; the field exists on `GetSources` and is populated by the builder from job/variable-derived config). Reaching the actual repeat sleep additionally requires `getSourcesOnce` to fail repeatedly (e.g., an unreachable/broken repo URL), which is also attacker-controllable by supplying a bad `CI_REPOSITORY_URL`/mirror configuration, or the job simply being canceled during a natural transient git failure. The bug is fully reproducible in a unit test with a canceled context and a stub `getSourcesOnce` that always errors — no external network flakiness is required.

### Recommendation
Make `SleepWithNotice` context-aware, e.g., change its signature to `SleepWithNotice(ctx context.Context, e *env.Env, d time.Duration) error` and implement with a `select` on `time.After(d)` and `ctx.Done()`, returning immediately (with `ctx.Err()`) on cancellation. Update `GetSources.Run` (and the other two callers in `artifact_download.go` and `cache_extract.go`) to pass `ctx` and break out of the retry loop immediately when the sleep is interrupted by cancellation.

### Proof of Concept
Go unit test in `functions/concrete/run/stages/get_sources_test.go`:
```go
func TestGetSources_Run_SleepIgnoresCancellation(t *testing.T) {
    e := newTestEnv(t, "bash")
    gs := GetSources{
        GitStrategy:                     "fetch",
        MaxAttempts:                     50,
        UseExponentialBackoffStageRetry: true,
        RepoURL:                         "https://invalid.invalid/nonexistent.git", // always fails getSourcesOnce
    }

    ctx, cancel := context.WithCancel(t.Context())
    go func() {
        time.Sleep(50 * time.Millisecond) // enter first sleep, then cancel
        cancel()
    }()

    start := time.Now()
    _ = gs.Run(ctx, e)
    elapsed := time.Since(start)

    // Cancellation happened almost immediately; if SleepWithNotice
    // respected ctx, Run should return within a few hundred ms of cancel.
    // Instead it blocks for the full backoff duration (>=5s per NewBackoff.Min).
    assert.Less(t, elapsed, 2*time.Second,
        "GetSources.Run should return promptly after context cancellation, got %v", elapsed)
}
```
Expected (current) behavior: the assertion fails because `elapsed` is at least ~5 seconds (the backoff `Min`), demonstrating the sleep is not interrupted by cancellation. After applying the fix (context-aware sleep), the test should pass with `elapsed` on the order of milliseconds after cancellation.

### Citations

**File:** functions/concrete/run/stages/internal/retry/retry.go (L21-24)
```go
func SleepWithNotice(e *env.Env, d time.Duration) {
	e.Noticef("Retrying in %v", d)
	time.Sleep(d)
}
```

**File:** functions/concrete/run/stages/get_sources.go (L148-167)
```go
	backoff := retry.NewBackoff()
	var err error
	for attempt := 1; attempt <= s.MaxAttempts; attempt++ {
		if attempt > 1 {
			if s.UseExponentialBackoffStageRetry {
				retry.SleepWithNotice(e, backoff.Duration())
			}
			e.Warningf("Retrying git fetch (attempt %d/%d)...", attempt, s.MaxAttempts)
			if s.ClearWorktreeOnRetry && attempt == 2 {
				if clearErr := s.clearWorktree(ctx, e); clearErr != nil {
					e.Warningf("Failed to clear worktree: %v", clearErr)
				}
			}
		}

		err = s.getSourcesOnce(ctx, e, gitEnv)
		if err == nil {
			break
		}
	}
```

### Title
`SleepWithNotice` ignores context cancellation, letting a large `GET_SOURCES_ATTEMPTS`/`MaxAttempts` value keep the executor slot occupied for minutes after a job is canceled - (File: functions/concrete/run/stages/internal/retry/retry.go)

### Summary
`retry.SleepWithNotice` performs a bare `time.Sleep(d)` with no `ctx.Done()` select, and `GetSources.Run` calls it inside its retry loop using an exponential backoff capped at 5 minutes. Because `GetSources.Run` executes inside the `prepare` phase under `jobCtx` (the job-level cancelable context), canceling a job mid-retry does not interrupt the sleep, keeping the worker goroutine—and therefore the executor's job slot—occupied well past cancellation.

### Finding Description
`GetSources.Run` loops up to `s.MaxAttempts` times, and on every attempt after the first calls `retry.SleepWithNotice(e, backoff.Duration())` when `UseExponentialBackoffStageRetry` is set: [1](#0-0) 

`SleepWithNotice` itself has no cancellation awareness: [2](#0-1) 

The backoff schedule used is jittered exponential with a 5-second minimum and a **5-minute maximum** per attempt: [3](#0-2) 

`GetSources.Run` is invoked from `Runner.prepare`, which runs under `jobCtx` — the same context that `Runner.Cancel()` cancels for job-level cancellation before script execution begins: [4](#0-3) 

Once `jobCtx` is canceled, the actual git operation inside `getSourcesOnce` (via `exec.CommandContext`-based `e.Command`) will fail quickly because the context is already done, so each loop iteration's real work is fast. However, the intervening `SleepWithNotice` call is a plain `time.Sleep`, which is completely deaf to `ctx.Done()`. The loop therefore continues sleeping the full (growing, up to 5-minute) backoff duration between each of the (attacker-influenced) `MaxAttempts` iterations, none of which check for cancellation before or during the sleep. No existing check (timeout guard, cancellation-aware select, or attempt cap validation) intercepts this path — the loop condition is only `attempt <= s.MaxAttempts`.

`MaxAttempts` is a job-configurable field on the `GetSources` struct (populated from the job's `GET_SOURCES_ATTEMPTS` CI/CD variable path through the builder), with no visible upper bound/clamp enforced in `get_sources.go` itself.

### Impact Explanation
When a pipeline author sets a large retry-attempt count and the job is canceled while `get_sources` is mid-retry (e.g., transient git fetch failures triggering the retry path), the goroutine executing that job's `prepare` stage will continue sleeping through each remaining backoff interval (up to 5 minutes each) instead of returning promptly on cancellation. This violates the invariant that cancellation must promptly release runner/executor resources: the executor's occupied slot (docker container, shell process, VM, k8s pod, etc.) is held for potentially many minutes to hours longer than expected, delaying or starving other jobs — including jobs from unrelated projects — queued on the same runner's limited concurrency slots.

### Likelihood Explanation
The precondition is straightforward and fully attacker-reachable: an unprivileged pipeline author sets a large `GET_SOURCES_ATTEMPTS` value and enables/relies on the exponential-backoff retry path, then cancels (or has canceled) the job while a git fetch/clone transiently fails and the loop is sleeping between attempts. No special privilege beyond normal CI configuration is required, and the behavior is deterministic and repeatable given the described conditions. The exact practical severity depends on any external clamp GitLab.com/the coordinator may apply to `GET_SOURCES_ATTEMPTS` before it reaches Runner (not visible in this Runner-side code), which is a genuine uncertainty in this analysis — I found no such clamp inside this repository's `get_sources.go`/`runner.go`/`builder.go`.

### Recommendation
Make `SleepWithNotice` (or its call sites) cancellation-aware, e.g. change its signature to accept a `context.Context` and use `select { case <-ctx.Done(): return ctx.Err(); case <-time.After(d): }`, and have `GetSources.Run` check/propagate that result to break out of the retry loop immediately instead of continuing to the next attempt.

### Proof of Concept
Go unit test in `functions/concrete/run/stages/get_sources_test.go`:
1. Construct a `GetSources` with `GitStrategy: "fetch"`, `UseExponentialBackoffStageRetry: true`, `MaxAttempts: 50`, and a `RepoURL` pointing at an unreachable host so `getSourcesOnce` fails fast on every attempt.
2. Create `ctx, cancel := context.WithCancel(context.Background())`, cancel it immediately (or after the first failed attempt), record `cancelTime := time.Now()`.
3. Call `s.Run(ctx, e)` and record `returnTime := time.Now()`.
4. Assert `returnTime.Sub(cancelTime)` is small (e.g., under 1 second) rather than accumulating multiple ~5s–5min backoff sleeps — the current implementation will fail this assertion, demonstrating the bug.

### Citations

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

**File:** functions/concrete/run/stages/internal/retry/retry.go (L11-19)
```go
// NewBackoff returns a 5s→5min jittered exponential-backoff schedule (factor 1.5).
func NewBackoff() *backoff.Backoff {
	return &backoff.Backoff{
		Min:    5 * time.Second,
		Max:    5 * time.Minute,
		Jitter: true,
		Factor: 1.5,
	}
}
```

**File:** functions/concrete/run/stages/internal/retry/retry.go (L21-24)
```go
func SleepWithNotice(e *env.Env, d time.Duration) {
	e.Noticef("Retrying in %v", d)
	time.Sleep(d)
}
```

**File:** functions/concrete/run/runner.go (L130-176)
```go
func (r *Runner) Cancel() {
	r.mu.Lock()
	defer r.mu.Unlock()

	if r.scriptCancel != nil {
		r.scriptCancel()
	}
}

// Run executes the full job lifecycle.
func (r *Runner) Run(ctx context.Context) error {
	jobCtx, jobCancel := r.withTimeout(ctx, r.config.Timeout)
	defer jobCancel()
	defer r.cleanup()

	// Before user scripts, Cancel() cancels the entire job.
	r.setCancel(jobCancel)

	if err := r.setupGitlabEnv(); err != nil {
		return fmt.Errorf("setting up GITLAB_ENV: %w", err)
	}

	if err := r.config.GetSources.SetupJobGitConfig(jobCtx, r.env); err != nil {
		return fmt.Errorf("setting up job git config: %w", err)
	}

	if err := r.prepare(jobCtx); err != nil {
		return err
	}

	scriptErr := r.executeSteps(jobCtx)
	cacheErr, artifactErr := r.finalize(jobCtx)

	return pickPriorityError(scriptErr, cacheErr, artifactErr)
}

func (r *Runner) setCancel(cancel context.CancelFunc) {
	r.mu.Lock()
	r.scriptCancel = cancel
	r.mu.Unlock()
}

//nolint:gocognit
func (r *Runner) prepare(ctx context.Context) error {
	if err := r.section(ctx, "get_sources", r.config.GetSources.Run); err != nil {
		return fmt.Errorf("fetching sources: %w", err)
	}
```

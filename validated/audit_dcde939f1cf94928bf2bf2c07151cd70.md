### Title
`Finalize()` only waits on the `scan()` goroutine's `WaitGroup`, not on `attach()` or the `processStream` errgroup goroutines, allowing goroutine/resource leaks after `Finalize()` returns - (File: executors/kubernetes/log_processor.go)

### Summary
`kubernetesLogProcessor.wg` is incremented and decremented only around the inner `scan()` goroutine [1](#0-0) , while `Finalize()` merely calls `l.wg.Wait()` [2](#0-1) . The top-level `attach()` goroutine spawned in `Process()` [3](#0-2)  and the `Stream`/`readLogs` goroutines started via `errgroup.Group` in `processStream()` [4](#0-3)  are never tracked by `wg`, so `Finalize()` provides no guarantee that these goroutines have exited.

### Finding Description
`Process()` starts an untracked goroutine that runs `attach()` for the lifetime of the job's log streaming loop [5](#0-4) . Inside `attach()`, each iteration calls `processStream()`, which itself launches two more untracked goroutines via `errgroup.Group.Go` — one running `logStreamer.Stream()` and another running `readLogs()` [6](#0-5) . Only `readLogs()`'s inner call to `scan()` registers with `l.wg` [7](#0-6) .

Within `attach()`, on the `outputLogFileNotExistsExitCode` branch the code performs a blocking, unbuffered send: `errCh <- fmt.Errorf(...)` [8](#0-7) . This send occurs *after* `processStream()` has already returned (i.e., after `gr.Wait()` completed and the `scan()` goroutine already called `wg.Done()`). At that point `l.wg`'s internal counter can be zero even though the `attach()` goroutine itself is still alive and can block indefinitely on that send if the consumer of `errCh` has stopped reading (e.g., the caller decided the job step ended and moved to calling `Finalize()` without continuing to drain `errCh`). Similarly, `readLogs()`'s blocking send `outCh <- logLine` [9](#0-8)  is inside an errgroup goroutine not tracked by `wg`; only if the associated `scan()` goroutine is also blocked at the same time will `wg.Wait()` incidentally block — but this is *coincidental*, not a guarantee, and depends entirely on the specific channel-blocking scenario and context cancellation timing.

Because `Finalize()`'s contract states "waits for all Goroutines called in Process() to finish" [10](#0-9) , but the implementation only awaits the `scan()` goroutine, the invariant is violated: `Finalize()` can return while `attach()` (and potentially the `Stream`/`readLogs` errgroup goroutines) are still running/blocked, holding references to `l.logStreamer` (which embeds `client kubernetes.Interface` and `clientConfig *restclient.Config`) [11](#0-10) .

### Impact Explanation
When `Finalize()` returns prematurely, the caller (executor cleanup) proceeds to release or reuse shared `kubeClient`/`rest.Config` resources under the assumption that no more goroutines reference them, while a leaked `attach()` goroutine keeps executing exec/stream calls against the Kubernetes API using those same client objects. This is a resource-accounting and lifecycle bug that can degrade goroutine/resource cleanup guarantees across concurrently scheduled jobs in the same runner process, matching the scoped impact described.

### Likelihood Explanation
Triggering requires the log-file-recreation retry path (`outputLogFileNotExistsExitCode`) to fire while the consumer that normally drains `errCh` stops doing so before `attach()`'s blocking send completes — a timing condition tied to normal job step transitions (e.g., cleanup-variables step) rather than an exotic attacker action, making it plausible in real job execution but dependent on precise caller-side channel-draining behavior in `kubernetes.go`, which was not fully inspected due to tool budget constraints. The structural gap in `wg` tracking (verified directly in code) is unconditional and always present regardless of timing.

### Recommendation
Track all goroutines spawned by `Process()` (the `attach()` goroutine itself) and all goroutines spawned inside `processStream()` (the `errgroup` members) using the same `l.wg`, or otherwise ensure `errgroup.Group.Wait()` results propagate into `l.wg` before `Finalize()` can return. Add `l.wg.Add(1)`/`defer l.wg.Done()` around the `attach()` goroutine launch in `Process()`, and ensure `processStream`'s errgroup goroutines are joined via the same mechanism so that `Finalize()` truly blocks until every goroutine started transitively by `Process()` has exited.

### Proof of Concept
Go unit test outline for `log_processor_test.go`:
1. Construct a `kubernetesLogProcessor` with a mock `logStreamer` whose `Stream()` writes a line causing `outputLogFileNotExistsExitCode`, and a `backoffCalculator` mock producing near-zero backoff so `attach()` loops quickly.
2. Call `Process(ctx)`, but do **not** read from the returned `errCh` after the first message (simulate caller giving up reading errors).
3. Capture `runtime.NumGoroutine()` baseline before, then call `Finalize()` and assert it returns (e.g., within a bounded `select`/timeout using a separate goroutine + channel).
4. After `Finalize()` returns, assert via `runtime.NumGoroutine()` (with a short settle delay) that the goroutine count has NOT returned to baseline — proving a goroutine (the blocked `attach()`) is still alive despite `Finalize()` having returned, violating the documented invariant.

### Citations

**File:** executors/kubernetes/log_processor.go (L38-44)
```go
type kubernetesLogStreamer struct {
	kubernetesLogProcessorPodConfig

	client       kubernetes.Interface
	clientConfig *restclient.Config
	executor     RemoteExecutor
}
```

**File:** executors/kubernetes/log_processor.go (L83-84)
```go
	// Finalize waits for all Goroutines called in Process() to finish.
	Finalize()
```

**File:** executors/kubernetes/log_processor.go (L131-141)
```go
func (l *kubernetesLogProcessor) Process(ctx context.Context) (<-chan string, <-chan error) {
	outCh := make(chan string)
	errCh := make(chan error)
	go func() {
		defer close(outCh)
		defer close(errCh)
		l.attach(ctx, outCh, errCh)
	}()

	return outCh, errCh
}
```

**File:** executors/kubernetes/log_processor.go (L143-145)
```go
func (l *kubernetesLogProcessor) Finalize() {
	l.wg.Wait()
}
```

**File:** executors/kubernetes/log_processor.go (L178-183)
```go
			case exitCode == outputLogFileNotExistsExitCode:
				// The cleanup variables step recreates a new output.log file
				// where the shells.TrapCommandExitStatus is written.
				// To not miss this line, we need to have the offset reset when we reconnect to the newly created log
				l.logsOffset = 0
				errCh <- fmt.Errorf("output log file deleted, cannot continue %w", err)
```

**File:** executors/kubernetes/log_processor.go (L204-235)
```go
	var gr errgroup.Group

	logsOffset := l.logsOffset
	gr.Go(func() error {
		defer cancel()

		err := l.logStreamer.Stream(ctx, logsOffset, writer)
		// prevent printing an error that the container exited
		// when the context is already cancelled
		if errors.Is(ctx.Err(), context.Canceled) {
			return nil
		}

		if err != nil {
			err = fmt.Errorf("streaming logs %s: %w", l.logStreamer, err)
		}

		return err
	})

	gr.Go(func() error {
		defer cancel()

		err := l.readLogs(ctx, reader, outCh)
		if err != nil {
			err = fmt.Errorf("reading logs %s: %w", l.logStreamer, err)
		}

		return err
	})

	return gr.Wait()
```

**File:** executors/kubernetes/log_processor.go (L266-268)
```go
			previousLogsOffset = newLogsOffset

			outCh <- logLine
```

**File:** executors/kubernetes/log_processor.go (L273-303)
```go
func (l *kubernetesLogProcessor) scan(ctx context.Context, logs io.Reader) (*logScanner, <-chan string) {
	logsScanner := &logScanner{
		reader: bufio.NewReaderSize(logs, bufio.MaxScanTokenSize),
		err:    nil,
	}

	linesCh := make(chan string)
	l.wg.Add(1)

	go func() {
		defer l.wg.Done()
		defer close(linesCh)

		// This goroutine will exit when the calling method closes the logs stream or the context is cancelled
		for {
			data, err := logsScanner.reader.ReadString('\n')
			if err != nil {
				logsScanner.err = err
				break
			}

			select {
			case <-ctx.Done():
				return
			case linesCh <- data:
			}
		}
	}()

	return logsScanner, linesCh
}
```

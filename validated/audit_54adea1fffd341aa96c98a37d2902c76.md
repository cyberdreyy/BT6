### Title
`raw.archiver.Archive` ignores `ctx` and can block `io.Copy` indefinitely on a blocking/infinite file source - (File: `commands/helpers/archive/raw/raw_archiver.go`)

### Finding Description
`Archive` accepts a `context.Context` parameter but never reads it: it calls `os.Open(pathname)` and then `io.Copy(a.w, f)` with no context wiring, no `ctx.Done()` check, and no deadline on the read loop [1](#0-0) . Compare this with the `tarzstd` archiver in the same package family, which explicitly checks `ctx.Err()` inside its per-file loop before copying [2](#0-1)  — the raw archiver has no equivalent check. The `Archiver` interface itself documents `ctx` as a real parameter meant to bound the operation [3](#0-2) .

However, tracing the only concrete caller I could find in this repo, `CacheArchiverCommand.createZipFile` invokes `archiver.Archive(context.Background(), c.files)` — it does not even forward a caller-supplied, cancellable context; it hardcodes `context.Background()` [4](#0-3) . This means even if the raw archiver *did* honor `ctx`, this call site would never cancel it anyway. The `--timeout` flag on `cache-archiver` only bounds the HTTP upload client (`NewCacheClient(c.Timeout)`), not the archiving step [5](#0-4) .

For the raw format specifically, `files` in `Archive` is a `map[string]os.FileInfo` populated upstream by the fileArchiver/glob logic from job-configured cache/artifact paths — but pathnames are constrained to files discovered under the job's working directory via `filepath.Walk`/glob resolution (as seen in the archiver tests using `filepath.Walk`) [6](#0-5) . I could not find, within the indexed code, a code path where an attacker-controlled `pathname` string is passed directly to `raw.archiver.Archive` bypassing this directory walk/glob resolution (e.g. resolving to `/proc/kcore` or `/dev/zero` outside the job's build/cache root). The raw archiver is registered for the `archive.Raw` format, used for single-file artifact/cache raw uploads, not general directory globbing.

### Impact Explanation
If reachable with an attacker-controlled path pointing to a blocking device or pipe, `io.Copy` would block the helper process's goroutine indefinitely, and because neither `Archive` nor its caller honor cancellation, the file descriptor and goroutine would leak past job cancellation — matching the scoped impact (resource leak affecting subsequent jobs). This is a real code defect (context ignored) worth fixing defensively. But without a demonstrated reachable path where a normal, unprivileged pipeline author can force an arbitrary/blocking pathname (as opposed to files legitimately present under the job's build directory from `paths:`/`artifacts:` config, which are regular files by the time they reach the archiver, since directory traversal already filters via `os.Stat`/`filepath.Walk`), this cannot be confirmed as an attacker-triggerable bug versus a general robustness gap.

### Likelihood Explanation
Uncertain/unconfirmed. The code-level absence of context handling is real and verifiable, but the exploit precondition — an unprivileged job being able to supply a `pathname` resolving to a FIFO or infinite-read device rather than a normal artifact/cache file discovered by the existing glob/walk logic — could not be established from the available index. This repo appears to be a modified/forked version of gitlab-runner (with `functions/concrete/run` abstractions not present in upstream GitLab Runner), so I cannot rule out that some caller in this fork passes less-validated paths to the raw archiver, but no such call site was found.

### Recommendation
Regardless of exploitability confirmation, harden `raw.archiver.Archive` defensively: check `ctx.Err()` before opening the file, and race `io.Copy` against `ctx.Done()` (e.g., wrap the copy in a goroutine and `select` on completion vs. context cancellation, closing the file to unblock the reader on cancellation). Additionally, fix `CacheArchiverCommand.createZipFile` to forward a real, cancellable/timeout-bound context instead of `context.Background()` so that `--timeout` (or job cancellation) actually bounds the archiving step, not just the upload step.

### Proof of Concept
```go
// commands/helpers/archive/raw/raw_archiver_ctx_test.go
func TestArchive_HonorsContextCancellation(t *testing.T) {
    r, w := io.Pipe() // writer never closes -> blocking reader
    defer w.Close()

    tmp := t.TempDir()
    fifoPath := filepath.Join(tmp, "blocking")
    // simulate a pathname whose reads never complete
    f, _ := os.Create(fifoPath)
    f.Close()

    ctx, cancel := context.WithCancel(context.Background())
    cancel() // already cancelled

    var buf bytes.Buffer
    a, _ := NewArchiver(&buf, tmp, archive.DefaultCompression)

    done := make(chan error, 1)
    go func() {
        fi, _ := os.Stat(fifoPath)
        done <- a.Archive(ctx, map[string]os.FileInfo{fifoPath: fi})
    }()

    select {
    case <-done:
        // expected: Archive returns promptly due to ctx cancellation
    case <-time.After(2 * time.Second):
        t.Fatal("Archive did not honor ctx.Done(); io.Copy blocked past cancellation")
    }
}
```
This test currently fails against the existing implementation for genuinely blocking sources (e.g., a real named pipe opened for read with no writer), demonstrating the missing cancellation wiring, but I was not able to confirm a production job-triggerable path that reaches this code with such an input.

### Citations

**File:** commands/helpers/archive/raw/raw_archiver.go (L35-49)
```go
func (a *archiver) Archive(ctx context.Context, files map[string]os.FileInfo) error {
	if len(files) > 1 {
		return ErrTooManyRawFiles
	}

	for pathname := range files {
		f, err := os.Open(pathname)
		if err != nil {
			return err
		}
		defer f.Close()

		_, err = io.Copy(a.w, f)
		return err
	}
```

**File:** commands/helpers/archive/tarzstd/tarzstd_archiver.go (L81-83)
```go
		if ctx.Err() != nil {
			return ctx.Err()
		}
```

**File:** commands/helpers/archive/archive.go (L47-50)
```go
// Archiver is an interface for the Archive method.
type Archiver interface {
	Archive(ctx context.Context, files map[string]os.FileInfo) error
}
```

**File:** commands/helpers/cache_archiver.go (L83-89)
```go
func (c *CacheArchiverCommand) getClient() *CacheClient {
	if c.client == nil {
		c.client = NewCacheClient(c.Timeout)
	}

	return c.client
}
```

**File:** commands/helpers/cache_archiver.go (L235-238)
```go
	err = archiver.Archive(context.Background(), c.files)
	if err != nil {
		return 0, err
	}
```

**File:** commands/helpers/archiver_test.go (L53-60)
```go
		files := make(map[string]fs.FileInfo)
		_ = filepath.Walk(dir, func(path string, info fs.FileInfo, err error) error {
			if info.IsDir() {
				return nil
			}
			files[path] = info
			return nil
		})
```

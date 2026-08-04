### Title
`Store.List()` bounds check compares base64-encoded line length against `NonceSize()` instead of decoded message length, allowing out-of-range panic on corrupted `masking.db` entries - (File: `commands/helpers/internal/store/store.go`)

### Summary
`List()` at store.go:98 validates `len(line)` (the raw base64 text, still containing the trailing newline) against `s.c.NonceSize()`, but then slices the *decoded* buffer `msg` at `s.c.NonceSize()` on line 102. Because base64 encoding expands data by ~4/3, a `line` long enough to pass the check can still decode to a `msg` shorter than `NonceSize()`, causing a slice-out-of-range panic instead of the intended graceful error.

### Finding Description
`List()` reads `masking.db` line by line, base64-decodes each line into `msg`, then does:
```go
if len(line) < s.c.NonceSize() {
    return results, fmt.Errorf("encrypted message length too small")
}
nonce, ciphertext := msg[:s.c.NonceSize()], msg[s.c.NonceSize():]
``` [1](#0-0) 

The check uses `line` (pre-decode, base64 text) rather than `msg` (post-decode bytes) as the bound. `NonceSize()` for the `chacha20poly1305.NewX` AEAD used here is 24 bytes. Since base64 encoding expands N decoded bytes to `4*ceil(N/3)` encoded bytes, a `line` of length 24 (which passes the `>= NonceSize()` check) can decode to roughly 18 bytes — well under 24 — making `msg[:24]` panic with "slice bounds out of range". This is a genuine mismatch between the validated variable and the one actually sliced, so the check provides essentially no protection against a short/corrupted decoded message.

`masking.db` is created and consumed by the `proxy-exec` helper command: `NewProxy` opens the store via `store.Open(dir)` where `dir` is `RUNNER_TEMP_PROJECT_DIR` (or `--temp-dir`), i.e., a directory inside the job's own build/temp area, and passes it into `addmask.New(db, stdout, stderr)`. [2](#0-1) [3](#0-2) 

Because `masking.db` lives inside the job's own temp/build directory, and `RUNNER_TEMP_PROJECT_DIR` is an environment variable visible/derivable by job scripts, an unprivileged job script that itself invokes `proxy-exec` (or otherwise has access to this directory before a later `proxy-exec` invocation in the same job) can write or truncate `masking.db` with an attacker-crafted short line, e.g. `"AAAAAAAAAAAAAAAAAAAAAAAA\n"` (24 base64 chars decoding to 18 bytes). The next helper invocation that opens the store and lists existing phrases will decode that line, pass the flawed length check, and panic on the slice operation.

### Impact Explanation
The panic crashes the `gitlab-runner-helper proxy-exec` process before it can wrap stdout/stderr with the masking writer, terminating that command invocation (denial of masking for that step) rather than the runner's main masking pipeline (job-log trace masking is a separate mechanism in the main runner process, not this store). The concrete, provable impact is a crash/DoS of the `proxy-exec` masking store for jobs that use this addmask-store mechanism; whether unmasked secret text reaches the terminal/log depends on how upstream callers handle the error/panic (a panic typically aborts before further writes), so classify as denial-of-masking-service on this component rather than confirmed direct leak of already-masked secrets.

### Likelihood Explanation
The precondition is limited: `masking.db` resides within the job's own writable temp/project directory, and only components using the `proxy-exec`/`addmask` mechanism read it via `List()`. An unprivileged job author who knows this internal file format could corrupt it between two `proxy-exec` invocations within the same job. This requires job-script knowledge of the internal store format and multiple `proxy-exec` calls in one job — a narrower but concrete exploit path.

### Recommendation
Fix the bounds check to validate the decoded buffer, not the encoded line:
```go
if len(msg) < s.c.NonceSize() {
    return results, fmt.Errorf("encrypted message length too small")
}
```
placed after the `base64.StdEncoding.DecodeString` call and before slicing `msg`.

### Proof of Concept
```go
func TestList_ShortDecodedMessage(t *testing.T) {
    // craft a masking.db with header + one line whose base64 length passes
    // len(line) >= NonceSize() but decodes to < NonceSize() bytes
    dir := t.TempDir()
    s, err := store.Open(dir)
    require.NoError(t, err)

    // directly append a corrupted line after the 32-byte key header
    short := make([]byte, 18) // < chacha20poly1305 NonceSize (24)
    line := base64.StdEncoding.EncodeToString(short) + "\n"
    _, err = s.RawFileAppendForTest(line) // or reopen underlying *os.File and Write
    require.NoError(t, err)

    _, err = s.List()
    require.Error(t, err) // expect graceful error, not panic
})
```
Expected today: the test panics with `runtime error: slice bounds out of range`; after the fix, `List()` returns `fmt.Errorf("encrypted message length too small")`.

### Citations

**File:** commands/helpers/internal/store/store.go (L93-102)
```go
		msg, err := base64.StdEncoding.DecodeString(line)
		if err != nil {
			return results, fmt.Errorf("decoding msg: %w", err)
		}

		if len(line) < s.c.NonceSize() {
			return results, fmt.Errorf("encrypted message length too small")
		}

		nonce, ciphertext := msg[:s.c.NonceSize()], msg[s.c.NonceSize():]
```

**File:** commands/helpers/proxy_exec.go (L40-59)
```go
type Proxy struct {
	store   *store.Store
	addmask *addmask.AddMask
}

func NewProxy(dir string, stdout, stderr io.Writer) (*Proxy, error) {
	db, err := store.Open(dir)
	if err != nil {
		return nil, err
	}

	pe := &Proxy{store: db}

	pe.addmask, err = addmask.New(db, stdout, stderr)
	if err != nil {
		return nil, err
	}

	return pe, nil
}
```

**File:** commands/helpers/proxy_exec.go (L80-93)
```go
	dst := os.Getenv("RUNNER_TEMP_PROJECT_DIR")
	if dst == "" {
		dst = c.TempDir
	}
	if c.Bootstrap {
		if err := bootstrap(dst); err != nil {
			logrus.Fatalln("bootstrapping", err)
		}
	}

	proxy, err := NewProxy(dst, stdout, stderr)
	if err != nil {
		logrus.Fatalln("creating exec proxy", err)
	}
```

### Title
Concurrent `Store.Open` calls sharing the same `dir` race on key-file check-then-act, causing key derivation mismatch - (File: commands/helpers/internal/store/store.go)

### Summary
`Open` performs two unguarded check-then-act sequences (`os.Stat(pathname)` → `os.WriteFile(keyPath, generateKey())`, and `info.Size()==0` → `f.Write(generateKey())`) with no cross-process locking. When two `proxy-exec` invocations for the same job target the same `dir` concurrently, these races can produce inconsistent `key1`/`key2` material between the two `Store` handles, and can also corrupt `masking.db` itself via concurrent appends.

### Finding Description
`Open` at [1](#0-0)  checks whether `masking.db` exists and, if not, (re)writes `keyPath` with a fresh random key. This check-then-act has no lock: if two processes both observe `os.Stat(pathname)` failing before either has created the file, both will call `os.WriteFile(keyPath, ...)`, and whichever write lands last wins, overwriting the other's `key2`.

Separately, `openFile` (in `store_unix.go`) opens the file with `O_APPEND|O_CREATE`, and back in `Open` at [2](#0-1)  both processes can observe `info.Size() == 0` simultaneously (TOCTOU) and both call `f.Write(generateKey())`. Because both file handles use `O_APPEND`, the kernel serializes the two 32-byte appends but does not prevent both from happening — the result is `masking.db` ending up with 64 bytes (two concatenated "key1" blobs) instead of one, corrupting the intended single-key1 file. `deriveEncryptionKey` at [3](#0-2)  reads `key1` via `io.ReadFull(f, key1[:])` from offset 0 of each process's own fresh handle (both see the same first 32 bytes, whichever append landed first), so `key1` is not the divergence point in most orderings — but the `keyPath` overwrite race described above still allows one process to read `key2_A` and the other to read `key2_B` from `os.ReadFile(keyPath)` at line 162, producing two different XOR-derived keys (`key1 ^ key2_A` vs `key1 ^ key2_B`) for the *same* `masking.db`.

`Add` (line 112) writes base64-encoded, chacha20poly1305-sealed lines using each `Store`'s own key; `List` (line 80) reads and attempts to `Open` (AEAD-decrypt) each line with its own key. If two `Store` instances derived different keys for the same file, decrypting lines written by the *other* process's key will fail with an AEAD authentication error at line 103-105 (`s.c.Open` returns error), not silently decrypt to plaintext/garbage.

### Impact Explanation
The realistic outcome of this race is a `List()`/decrypt **error** (AEAD auth failure) or file corruption (interleaved raw key bytes breaking the base64/newline framing assumed by `List`), not "garbage decrypted secrets exposed as valid data" and not "the wrong process's masking phrase silently substituted for another's." An AEAD failure causes `List()` to return an error, which the caller (`addmask.New`, an external dependency not present in this repo, so its exact failure-handling behavior cannot be verified from this codebase) would need to handle. Whether that failure path causes masking to silently no-op (leaking secrets unmasked) or causes `proxy-exec` to abort the job step cannot be confirmed here, because `phrasestream/addmask` is an external module not vendored in this repository. Without visibility into that call site's error handling, the claim that this concretely results in "secrets leaking unmasked in trace output" is not something this repository's code can substantiate — it's a plausible downstream consequence, but unproven within the scope of `store.go`.

Additionally, the premise that this occurs "for parallel job steps" of the *same* job is questionable: `NewProxy`/`Open` is invoked once per `proxy-exec` process with `dir` derived from `RUNNER_TEMP_PROJECT_DIR`/`--temp-dir`, which is set per job/build directory, not obviously shared concurrently across parallel steps within a single job in the reachable code shown here.

### Likelihood Explanation
Reproducing the race requires two `Store.Open` calls to genuinely overlap on the same `dir` before `masking.db` is created — a narrow timing window (microseconds, between `os.Stat` and file creation via `os.OpenFile(O_CREATE)`), not something an unprivileged pipeline author can reliably trigger. It is a legitimate concurrency bug in the check-then-act pattern, but exploitability by an attacker (as opposed to being a self-inflicted reliability bug under normal Runner operation) is not established, and no code path in this repo is shown to spawn two `proxy-exec` processes against the same directory concurrently by design.

### Recommendation
Serialize `Open` per `dir` using a file lock (e.g., `flock` on `keyPath` or a sentinel lock file) around the check-then-act sequences for both `keyPath` creation and the initial `key1` write, so only one process performs key generation while others wait and then read the already-established key material.

### Proof of Concept
A test spawning two goroutines calling `store.Open(sameDir)` concurrently (with an injected delay between `os.Stat` and `os.WriteFile`/`f.Write` to force the interleaving) could assert that both resulting `Store` handles derive equal encryption keys (comparable indirectly by asserting that a phrase added via handle A appears in `List()` from handle B). This would require test-only instrumentation (a hook/sleep) to reliably force the race window, since the natural window is too small to hit deterministically — indicating the bug, while theoretically present in the code, is not trivially reproducible without artificial timing control. [4](#0-3) [5](#0-4)

### Citations

**File:** commands/helpers/internal/store/store.go (L29-61)
```go
func Open(dir string) (*Store, error) {
	pathname := filepath.Join(dir, "masking.db")
	sum := sha256.Sum256([]byte(pathname))
	keyPath := filepath.Join(dir, "runner"+hex.EncodeToString(sum[:]))

	_ = os.MkdirAll(filepath.Dir(pathname), 0o755)
	_, err := os.Stat(pathname)
	if err != nil {
		// store file doesn't exist, so re-generate key
		if err := os.WriteFile(keyPath, generateKey(), 0o644); err != nil {
			return nil, fmt.Errorf("writing key: %w", err)
		}
	}

	f, err := openFile(pathname)
	if err != nil {
		return nil, fmt.Errorf("opening store file: %w", err)
	}

	info, err := f.Stat()
	if err != nil {
		return nil, fmt.Errorf("stat store file: %w", err)
	}

	if info.Size() == 0 {
		if _, err := f.Write(generateKey()); err != nil {
			return nil, fmt.Errorf("writing store key: %w", err)
		}
		_, _ = f.Seek(0, io.SeekStart)
		if err := f.Sync(); err != nil {
			return nil, err
		}
	}
```

**File:** commands/helpers/internal/store/store.go (L156-175)
```go
func deriveEncryptionKey(f *os.File, keyPath string) ([]byte, error) {
	var key1 [32]byte
	if _, err := io.ReadFull(f, key1[:]); err != nil {
		return nil, err
	}

	key2, err := os.ReadFile(keyPath)
	if err != nil {
		return nil, err
	}

	if len(key2) < len(key1) {
		return nil, fmt.Errorf("key1 and key2 not the same size")
	}

	for i := 0; i < len(key1); i++ {
		key1[i] ^= key2[i]
	}

	return key1[:], nil
```

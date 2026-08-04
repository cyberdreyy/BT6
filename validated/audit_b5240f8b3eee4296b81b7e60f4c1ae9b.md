### Title
Fast-path `bytes.Equal(p, mask)` shortcut in `masker.Write` bypasses phrase-matching without resetting/flushing partial-match state, allowing secret content containing the literal `[MASKED]` to desync masking - (File: `common/buildlogger/internal/masker/masker.go`)

### Summary
The `masker.Write` fast path at [1](#0-0)  forwards any write whose entire buffer is byte-identical to the literal `[MASKED]` straight to the next writer, without touching `m.matching`. If this fast path fires while the same masker instance is mid-way through matching a real secret phrase (`m.matching > 0`, held from a previous `Write()` call), the pending bytes are neither flushed nor cleared, and any portion of the real secret that happens to coincide with the 8-byte `[MASKED]` string is emitted verbatim, bypassing the phrase state machine entirely for that chunk.

### Finding Description
`masker.New` builds a chain of per-phrase `masker` writers [2](#0-1) . Each `masker.Write` tracks partial matches across `Write()` calls via the `m.matching` counter, deferring emission of matched-so-far bytes until either a full match completes (replaced with `[MASKED]`, line 103) or the match fails and the buffered prefix is flushed as-is (lines 115-127). The state machine's correctness depends on every byte of the input stream passing through the `bytes.HasPrefix` comparison against `m.phrase[m.matching:]` in sequence.

The fast path at lines 60-64 breaks this invariant: `if bytes.Equal(p, mask) { return m.next.Write(p) }` unconditionally short-circuits the entire matching loop for any Write() call whose buffer is exactly the 8-byte sentinel `[MASKED]` - regardless of whether `m.matching` is currently non-zero. This check exists to let an inner masker's own recursive `m.Write(mask)` call (line 103) propagate the replacement token down the chain to subsequent maskers without those maskers re-scanning it. But it does not distinguish "this is our own already-produced replacement token" from "this happens to be a raw chunk of upstream secret content that byte-for-byte equals `[MASKED]`."

If a resolved secret's actual value (e.g., an attacker-controlled GCP Secret Manager value reached via `handleSecret` -> `spec.Variable{Masked:true}`) is echoed by job script output and, due to executor/pipe read()-chunking, a piece of it arrives as its own standalone `Write()` call that is exactly `[MASKED]` while the masker for that same phrase is still holding a pending partial match from an earlier chunk, that chunk is written straight to `m.next` unmodified and unmasked, and `m.matching` is left unchanged/dangling. Any subsequent real continuation bytes are then compared against the phrase at the stale offset as if the intervening chunk never happened, producing a corrupted match: the raw secret substring bypasses masking entirely for that chunk, and the surrounding output can still show `[MASKED]` for the remainder, giving a spliced partially-unmasked/partially-masked leak of a real secret.

No other check in the pipeline (allowed-image checks, path validation, etc.) applies here since masking is the last line of defense for secret values echoed to the trace; the bug is purely in `masker.go`'s state machine.

### Impact Explanation
A chunk of a real masked secret (job token, CI/CD variable, or attacker-influenced resolved GCP secret value) can be written unmasked into the job trace/log when it happens to align with the `[MASKED]` sentinel at a `Write()` boundary, violating the "secrets/tokens must not leak ... in logs/traces" invariant. The leaked chunk size is bounded (up to the length overlapping `[MASKED]`), but any leaked bytes of a token/secret can materially reduce brute-force/reconstruction difficulty or directly expose sensitive fragments.

### Likelihood Explanation
Exploitation requires: (1) attacker-influenced secret content that either contains the literal `[MASKED]` substring or interacts with concurrently masked values whose internal `Write(mask)` propagation lands mid-match on another phrase, and (2) the underlying trace-writer chunking to produce a standalone `Write()` call whose buffer is exactly the 8-byte string, while `m.matching>0` for that same masker. Condition (2) depends on executor I/O chunking (pipe/pty read granularity) which is not fully attacker-controlled in general, but can plausibly be influenced via separated print/flush operations in job scripts (a common technique for forcing distinct write() syscalls). This makes the bug realistically, if narrowly, triggerable rather than purely theoretical, and it is deterministically reproducible once the Write() boundaries are set up as above.

### Recommendation
Remove or tighten the fast path so it only applies to the masker's own internally-generated replacement forwarding, not to arbitrary equal-content writes from upstream. E.g., mark internally-generated `[MASKED]` writes with a distinct signal (a dedicated method such as `writeMask()` that bypasses only when called internally, rather than comparing raw bytes), or at minimum, guard the fast path with `m.matching == 0` and otherwise process the buffer through the standard matching loop even when it equals the mask literal.

### Proof of Concept
Go unit/fuzz test in `common/buildlogger/internal/masker/masker_test.go`:
```go
func TestMasking_MaskLiteralDesync(t *testing.T) {
    buf := new(bytes.Buffer)
    secret := "AB[MASKED]CD" // secret containing the sentinel literal
    m := New(internal.NewNopCloser(buf), [][]byte{[]byte(secret)})

    // Simulate a chunk boundary landing mid-phrase, then a standalone
    // write that is exactly "[MASKED]" (part of the real secret bytes).
    n1, err := m.Write([]byte("AB"))
    require.NoError(t, err)
    require.Equal(t, 2, n1)

    n2, err := m.Write([]byte("[MASKED]")) // exact 8-byte sentinel, real secret bytes
    require.NoError(t, err)
    require.Equal(t, 8, n2)

    n3, err := m.Write([]byte("CD"))
    require.NoError(t, err)
    require.Equal(t, 2, n3)

    require.NoError(t, m.Close())

    // Assert the raw secret substring never appears unmasked in output.
    assert.NotContains(t, buf.String(), "AB[MASKED]CD")
    assert.NotContains(t, buf.String(), "[MASKED]CD") // partial leak check
}
```
Expected (buggy) result: the output buffer contains a spliced/partial leak (e.g. `"[MASKED]CD"` unmasked remainder) instead of a single `"[MASKED]"` replacing the whole secret, demonstrating the desync and unmasked leakage of secret-derived bytes.

### Citations

**File:** common/buildlogger/internal/masker/masker.go (L27-37)
```go
func New(w io.WriteCloser, phrases [][]byte) *Masker {
	m := &Masker{}
	m.next = w

	// Create a masker for each unique phrase
	for i := 0; i < len(phrases); i++ {
		m.next = &masker{next: m.next, phrase: phrases[i]}
	}

	return m
}
```

**File:** common/buildlogger/internal/masker/masker.go (L60-64)
```go
	// fast path: if the write is "[MASKED]" from an upper-level, don't bother
	// processing it, send it to the next writer.
	if bytes.Equal(p, mask) {
		return m.next.Write(p)
	}
```

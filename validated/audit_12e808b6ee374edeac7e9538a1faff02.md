### Title
Cross-Write() phrase restart logic is not full KMP and can fail to mask a secret split across chunk boundaries - (File: common/buildlogger/internal/masker/masker.go)

### Summary
`masker.Write`'s restart branch at lines 121-127 only checks whether the single byte that broke a partial match equals `m.phrase[0]`, rather than computing the correct KMP failure-function shift for the already-matched prefix. For phrases whose self-overlap ("border") is longer than one character (e.g. `"aab"`), this causes the writer to permanently flush already-matched bytes as unmasked plaintext at line 116 before it can recognize that those bytes were still part of a valid overlapping match, so the phrase is missed entirely when a `Write()` call boundary happens to land inside that overlap region.

### Finding Description
`masker.Write` matches `m.phrase` incrementally across `Write()` calls, keeping `m.matching` as the count of bytes matched so far [1](#0-0) . When a partial match fails, it unconditionally writes out `m.phrase[:m.matching]` as unmasked data (line 116), then tries a single-byte restart heuristic: if the byte that caused the mismatch equals `m.phrase[0]`, it sets `m.matching = 1` and continues [2](#0-1) .

This is not equivalent to the KMP failure function. Tracing phrase `"aab"` split across two `Write()` calls as `"aa"` then `"ab"`:
- Write("aa"): `bytes.HasPrefix` matches phrase[0:2]="aa" (limited by buffer length), so `m.matching` becomes 2, nothing is emitted yet.
- Write("ab"): `min = len(phrase[2:]) = 1`; `HasPrefix("a", "b")` fails. The code writes `phrase[:2]="aa"` unmasked (line 116). It then checks `phrase[0]=='a' == p[n]=='a'` (true), so it resets `matching=1` and advances past that byte, discarding the earlier matched `'a'` at the previous position that is also a valid start of an overlapping match.
- The remaining `"b"` then fails against `phrase[1]='a'`, so it too is flushed unmasked.

Combined output across both calls is `"aaab"` — byte-for-byte identical to the raw, unmasked input — even though the substring `"aab"` (indices 1-3 of the concatenated stream) is exactly the secret phrase and should have been replaced (a single-buffer write of `"aaab"` correctly produces `"a[MASKED]"`). This is a genuine loss-of-masking, not merely a byte-count discrepancy: no `[MASKED]` token is ever emitted for a phrase occurrence that legitimately exists in the log stream, purely because of where the write boundary fell.

The repository already contains a masking fuzz harness [3](#0-2)  that randomly chunks writes and asserts the mask phrase never appears in output [4](#0-3) , but its phrase corpus only contains phrases with trivial (length ≤ 1) self-overlap borders or pure single-character repeats (`"AAAA…"`, `"secret"`, `"ssecret"`, `"secrett"`), none of which exercise a border of length ≥ 2 combined with a differing trailing character the way `"aab"` does. Pure repeated-character phrases don't trigger this failure mode because every byte equal to the repeated character keeps extending the match rather than mismatching. As a result, this specific class of overlap is not covered by the existing fuzz corpus and appears to be a genuine, previously unexercised gap.

### Impact Explanation
If a masked CI/CD variable's value contains an internal repeat pattern with a border ≥ 2 characters (plausible for many real-world secrets/tokens, e.g. ones containing repeated substrings), and the job's own stdout happens to be delivered to the trace writer in chunks that split exactly inside that overlap region, the masker can fail to redact the secret, letting it appear in cleartext in the job log/trace. This directly violates the core invariant that masked secret values must not leak into job traces/logs.

### Likelihood Explanation
Reaching this requires two things to align: (1) the secret's byte content must contain a self-overlapping substring of length ≥ 2 (not guaranteed, but not rare either for base64/hex-like tokens), and (2) the job's stdout must be captured by the trace pipeline in chunks that split precisely inside that overlap window. An unprivileged job author fully controls their own stdout timing/flushing (e.g. via `printf`, partial writes, explicit flush/sleep between prints) and can attempt to force distinct reads at chosen byte offsets when deliberately trying to print a variable it has access to (e.g. via `env`/`printenv`, or copy-pasting into debug output) — but without knowing the exact secret bytes in advance, aligning the split precisely is non-trivial and would generally require repeated attempts or an oracle (comparing masked vs. unmasked output across many pipeline runs) to locate the right split point. This makes the bug real and reproducible in a controlled test, but its practical exploitability against an unknown secret value is probabilistic/laborious rather than a deterministic one-shot bypass.

### Recommendation
Replace the single-byte "does the failing byte equal phrase[0]" heuristic with a proper KMP failure-function (prefix-function) based restart: on mismatch after matching `k` bytes, compute the longest proper border of `phrase[:k]` and resume matching from that border length against the current byte, rather than discarding all previously tracked bytes and only checking a length-1 restart. Alternatively, buffer any bytes that could still be part of an overlapping match instead of eagerly flushing `phrase[:m.matching]` at line 116 before the overlap has been fully resolved. Add masker unit/fuzz test phrases with non-trivial internal borders (e.g. `"aab"`, `"abab"`, `"aabaa"`) combined with adversarial chunk splits to catch regressions.

### Proof of Concept
Go unit test in `common/buildlogger/internal/masker/masker_test.go`:
```go
func TestMasker_OverlappingPhraseSplitAcrossWrites(t *testing.T) {
    buf := &bytes.Buffer{}
    w := masker.New(nopWriteCloser{buf}, [][]byte{[]byte("aab")})

    // Same logical stream as a single write masks correctly:
    single := &bytes.Buffer{}
    ws := masker.New(nopWriteCloser{single}, [][]byte{[]byte("aab")})
    ws.Write([]byte("aaab"))
    ws.Close()
    require.Equal(t, "a[MASKED]", single.String())

    // Split at the byte boundary that lands inside the phrase's internal border ("aa"|"ab"):
    w.Write([]byte("aa"))
    w.Write([]byte("ab"))
    w.Close()

    // Expect masking parity with the single-write case; currently fails,
    // producing "aaab" (secret phrase fully unmasked).
    require.NotContains(t, buf.String(), "aab", "secret phrase leaked unmasked due to chunk-boundary restart bug")
    require.Equal(t, "a[MASKED]", buf.String())
}
```
Additionally extend the existing `Fuzz` corpus in `common/buildlogger/internal/build_logger_fuzz.go` with phrases containing non-trivial borders (e.g. `"aab"`, `"abab"`) to catch this class of bug under randomized chunking, differential-testing against the known-good single-buffer masking result.

### Citations

**File:** common/buildlogger/internal/masker/masker.go (L48-52)
```go
type masker struct {
	phrase   []byte
	matching int
	next     io.WriteCloser
}
```

**File:** common/buildlogger/internal/masker/masker.go (L113-127)
```go
		// if we didn't complete a phrase match, send the tracked bytes of
		// the phrase to the next writer unmodified.
		if m.matching > 0 {
			_, err = m.next.Write(m.phrase[:m.matching])
			if err != nil {
				return n, err
			}

			// if the end of this phrase matches the start of it, try again
			if m.phrase[0] == p[n] {
				m.matching = 1
				last++
				n++
				continue
			}
```

**File:** common/buildlogger/internal/build_logger_fuzz.go (L25-35)
```go
func Fuzz(data []byte) int {
	phrases := [][]byte{
		bytes.Repeat([]byte{'A'}, 1024),
		bytes.Repeat([]byte{'B'}, 4*1024),
		bytes.Repeat([]byte{'C'}, 8*1024),
		[]byte("secret"),
		[]byte("secret_suffix"),
		[]byte("ssecret"),
		[]byte("secrett"),
		[]byte("ssecrett"),
	}
```

**File:** common/buildlogger/internal/build_logger_fuzz.go (L90-95)
```go
	contents := buf.Bytes()
	for _, mask := range phrases {
		if bytes.Contains(contents, mask) {
			panic(fmt.Sprintf("mask %q present in %q", mask, contents))
		}
	}
```

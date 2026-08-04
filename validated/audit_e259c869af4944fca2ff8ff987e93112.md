### Title
URLSanitizer masks a token incorrectly when write is split exactly at the key/value boundary for the longest known key - (File: common/buildlogger/internal/urlsanitizer/urlsanitizer.go)

### Summary
The specific mechanism alleged in the question — the `off+len(s.match) > cap(s.match)` bail at `urlsanitizer.go:122` — is not actually exploitable: it only rejects keys that are genuinely longer than any known token key, and I could not construct a case where it produces a false negative for a real `x-amz-*` key split across `Write()` calls. However, a related but distinct bug exists in the same state machine: the unconditional cap-reset at the top of the loop (`urlsanitizer.go:82-84`) fires and discards a **fully accumulated, exactly-matching** key before the code gets a chance to check whether the next byte is `=`, if the chunk boundary falls precisely between the end of the key name and the `=` sign — but only for the single longest key in `tokenParamKeys`, `x-amz-security-token` (20 chars), because `cap(s.match)` is sized to `len(longest)+1`.

### Finding Description
`New()` sizes `s.match` capacity to `len(longest token)+1` [1](#0-0) , i.e. 21 (`"x-amz-security-token"` is 20 chars, plus the leading `?`/`&`). At the top of `Write`'s loop, before any terminator check, the code does:
```
if len(s.match) == cap(s.match) {
    s.match = s.match[:0]
}
``` [2](#0-1) 

If a `Write()` call ends exactly when `s.match` has accumulated the full 21-byte key `"?x-amz-security-token"` (no `=`/`?`/`&` seen yet, since the chunk boundary lands right after the key name and before `=`), the state is persisted across calls with `len(s.match) == cap(s.match) == 21`. On the *next* `Write()` call (which begins with `=<secret>`), the very first thing the loop does is reset `s.match` to empty — discarding the fully-matched key identity — before it ever inspects the `=` that follows. The fast path then re-scans for the next `?`/`&`, treats an unrelated `&` (e.g. the terminator of the secret value itself, or a later param) as a new key start, and the entire `=<secret>&` span is written to the wrapped writer (`tokensanitizer` → `timestamper` → trace) unmasked, because `s.masking` was never set to `true` and `last` was never advanced past `0`.

This bypasses masking specifically for `x-amz-security-token` (AWS STS temporary session token used with S3-compatible caches configured via IAM/IRSA), because it is the only entry in `tokenParamKeys` whose length makes a full match land exactly on `cap(s.match)`. Shorter keys (`x-amz-signature`, `x-amz-credential`, `private_token`, etc.) never reach `cap(s.match)` on a clean full match, so they are not affected by this specific reset ordering.

Existing tests (`urlsanitizer_test.go`) exhaustively split `x-amz-credential` at arbitrary byte offsets (lines 94-129) but do not test a split landing exactly between the end of `x-amz-security-token` and `=`, so this gap is not currently covered.

The GCS-specific part of the question's premise does not hold today: `tokenParamKeys` at `urlsanitizer.go:16-30` contains no `x-goog-signature`/`x-goog-*` entry, so GCS presigned URL parameters are not masked at all today — that is a feature gap, not a chunking bypass of an existing protection, and is out of scope for "streaming/chunking bug" analysis.

### Impact Explanation
If a job's stdout is flushed in small chunks such that the chunk boundary falls exactly between the AWS STS temp-token key name and its `=`, the `x-amz-security-token` value (an AWS temporary session token) is written to the job trace unmasked instead of `[MASKED]`. Anyone with read access to that job's trace (which may be a wider audience than the token's intended scope, depending on visibility settings, protected-branch policies, or artifact/log retention) could reuse the leaked STS token/presigned URL parameters before expiry to access the S3 cache backend.

### Likelihood Explanation
This requires: (1) the runner-produced or job-produced output to actually contain an `x-amz-security-token=...` presigned URL fragment in the stdout/stderr stream being sanitized, and (2) that content to be delivered to `Write()` split at the exact byte boundary between the key name and `=`. Condition (2) is plausible under unbuffered/line-unbuffered output (e.g. `printf` without newline, or partial reads from a pipe) but requires a precise split point (1-in-N chance per byte position for naturally chunked output, or deterministic if an attacker controls output timing/buffering directly, e.g. via a custom script deliberately flushing at that offset). It is reliably reproducible via a targeted unit test.

### Recommendation
Move the terminator/`=` check ahead of the cap-based reset, or defer the cap reset until after confirming the current byte is not `=`/`?`/`&`. Concretely, only reset `s.match` when it is at capacity **and** the current lookup for a terminator inside the fast/slow paths fails to find one in this same call — or grow the capacity check to check `len(s.match) == cap(s.match)` only after first checking whether `p[n]` (if any bytes remain) is `=`. A simpler fix: when `len(s.match) == cap(s.match)` at loop entry, first check `n < len(p) && p[n] == '='` before resetting, and if so proceed to the key-matching logic instead of discarding state.

### Proof of Concept
```go
func TestMasking_SplitExactlyAtKeyValueBoundary_LongestKey(t *testing.T) {
    buf := new(bytes.Buffer)
    m := New(internal.NewNopCloser(buf))

    // "?x-amz-security-token" is exactly cap(s.match) (21) bytes.
    n1, err := m.Write([]byte("prefix ?x-amz-security-token"))
    require.NoError(t, err)
    assert.Equal(t, len("prefix ?x-amz-security-token"), n1)

    n2, err := m.Write([]byte("=SECRETVALUE&suffix"))
    require.NoError(t, err)
    assert.Equal(t, len("=SECRETVALUE&suffix"), n2)

    require.NoError(t, m.Close())
    assert.Equal(t, "prefix ?x-amz-security-token=[MASKED]&suffix", buf.String())
    // Expected to FAIL today: buf.String() will contain the literal
    // "=SECRETVALUE&" unmasked because s.match is reset before the '='
    // is inspected.
}
```
A fuzz-style companion test should iterate the split index over the full string `"?x-amz-security-token=SECRETVALUE&"` (as the existing `TestMasking` does with `|`-delimited split markers for `x-amz-credential`) and assert `[MASKED]` appears at every split point — this will reveal the failure specifically at the split index equal to `len("?x-amz-security-token")`.

### Citations

**File:** common/buildlogger/internal/urlsanitizer/urlsanitizer.go (L41-50)
```go
func New(w io.WriteCloser) *URLSanitizer {
	var max int
	for token := range tokenParamKeys {
		if len(token) > max {
			max = len(token) + 1
		}
	}

	return &URLSanitizer{w: w, match: make([]byte, 0, max)}
}
```

**File:** common/buildlogger/internal/urlsanitizer/urlsanitizer.go (L80-84)
```go
		// if our match is at capacity (maximum token size), reset it and
		// continue looking for the next token.
		if len(s.match) == cap(s.match) {
			s.match = s.match[:0]
		}
```

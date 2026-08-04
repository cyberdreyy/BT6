### Title
Substring-overlapping mask phrases can produce incomplete masking / residual secret leakage - (File: common/buildlogger/internal/masker/masker.go)

### Summary
`masker.New()` builds a chain of per-phrase writers in the raw order of the `phrases` slice without sorting by length, so the *last* phrase in the slice becomes the outermost (first-processed) writer. When one mask phrase is a substring of another (e.g. `"AB"` and `"ABC"`), if the shorter phrase ends up outer relative to the longer one, the shorter masker consumes and replaces its match before the longer masker ever sees the full string, leaving the residual bytes of the longer secret un-masked in the output (e.g. `"[MASKED]C"` instead of `"[MASKED]"`).

### Finding Description
`masker.New()` iterates `phrases` in slice order and wraps writers so that the writer created *last* is closest to `Masker.Write` (i.e., outermost/first to see data), and the writer created *first* sits innermost, right above the underlying writer `w`: [1](#0-0) 

There is no sorting by phrase length here, despite the package doc comment claiming writers are "stacked... in length order, starting with the longest": [2](#0-1) 

Each per-phrase `masker.Write` greedily matches its own phrase byte-by-byte and, on a full match, immediately replaces it with `[MASKED]` and forwards that literal token downstream (fast-pathed via `bytes.Equal(p, mask)`), while any non-matching trailing bytes are forwarded unmodified to the next writer in the chain: [3](#0-2) [4](#0-3) 

If phrases are `["ABC", "AB"]`, `"AB"` (last element) becomes the outer masker and `"ABC"` becomes inner. Feeding `"ABC"`: the outer `"AB"` masker matches and masks the first two bytes, emitting `"[MASKED]"` downstream, then separately forwards the leftover `"C"` to the inner `"ABC"` masker, which cannot match `"ABC"` against a lone `"C"` and passes it through untouched. Final output: `"[MASKED]C"` — the trailing character of the actual secret value is leaked into the log/trace.

The mask phrases fed into `masker.New` come from `Logger.maskPhrases`, built via `internal.Unique(opts.MaskPhrases)`, which only deduplicates and preserves input order — it does not sort by length: [5](#0-4) [6](#0-5) 

`opts.MaskPhrases` ultimately derives from job/CI variable values marked as masked; if two masked variable values happen to be substrings of one another (a realistic scenario, e.g. an API token and a truncated/prefix variant of it, or a masked variable value that coincidentally contains another masked value as a prefix), whichever order they arrive in the job payload determines whether the shorter or longer phrase's masker ends up outer, and thus whether trailing bytes of the longer secret leak.

None of the existing checks (allowed images, path validation, auth checks) address this — masking correctness is the only control here, and it is order-dependent rather than length-invariant.

### Impact Explanation
This is a genuine violation of the core invariant that "secrets, tokens, and masked values must not leak... in logs/traces." A job whose masked variable set contains overlapping substrings can, depending on the arrival order of those variables in the job payload, cause the runner to emit a partially-unmasked residual of a supposedly fully-masked secret directly into the build log/trace, which is visible to anyone with read access to job logs. The leaked residual is limited to the non-overlapping suffix bytes of the longer phrase — it is a partial, not full leak — but it does expose real secret structure/content bytes that should have been fully redacted.

### Likelihood Explanation
Preconditions: at least two mask phrases registered for the job where one is a substring/prefix of the other, and the shorter one lands later in the `MaskPhrases` slice than the longer one (making it the outer/first-processed masker). This is a plausible, not contrived, configuration — e.g. two masked CI/CD variables where one value is a truncated form of another, or reused/rotated tokens sharing a common prefix. The bug is deterministic given a fixed phrase order, and reproducible with a minimal unit test directly against `masker.New`.

### Recommendation
Sort `phrases` by descending length (and tie-break deterministically) before constructing the writer chain in `masker.New`, so the longest overlapping phrase is always the outermost/first-processed masker, guaranteeing longer matches take priority over substrings. Add a regression test that combinatorially permutes overlapping phrase sets and asserts no residual/partial-mask bytes appear in the output for any registration order.

### Proof of Concept
```go
package masker_test

import (
	"bytes"
	"testing"

	"gitlab.com/gitlab-org/gitlab-runner/common/buildlogger/internal"
	"gitlab.com/gitlab-org/gitlab-runner/common/buildlogger/internal/masker"
)

func TestMasker_SubstringOverlap_OrderDependentLeak(t *testing.T) {
	var buf bytes.Buffer
	// "AB" is a substring of "ABC"; here "AB" is last, so it becomes the
	// outer (first-processed) writer.
	w := masker.New(internal.NewNopCloser(&buf), [][]byte{[]byte("ABC"), []byte("AB")})

	_, _ = w.Write([]byte("ABC"))
	_ = w.Close()

	got := buf.String()
	// Expected (secure) behavior: full phrase masked, no residual bytes.
	if got != "[MASKED]" {
		t.Fatalf("residual secret leaked: got %q, want %q", got, "[MASKED]")
	}
}
```
Running this test against the current implementation produces `got = "[MASKED]C"`, failing the assertion and demonstrating the residual-leak bug. A follow-up combinatorial test permuting `[][]byte{"AB","ABC"}` in both orders and asserting the no-residual invariant on the output for every permutation would fully cover the reported concern.

### Citations

**File:** common/buildlogger/internal/masker/masker.go (L1-12)
```go
// Package masker implements a masking Writer, where specified phrases are
// replaced with the word "[MASKED]".
//
// To achieve masking over Write() boundaries, each phrase has its own writer.
// These writers are stacked, with each one calling the next, in length order,
// starting with the longest. This allows each writer to scan for their phrase
// in-turn, filtering data down to the next writer as required.
//
// Each mask writer tracks when its phrase is being written, and counts until
// either it's matched all bytes of the phrase, and then replaces it, or if a
// full match isn't found, sends the matched bytes to the next writer
// unmodified.
```

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

**File:** common/buildlogger/internal/masker/masker.go (L88-111)
```go
		// try to match the next part of the phrase
		if bytes.HasPrefix(p[n:], m.phrase[m.matching:m.matching+min]) {
			// send any data that we've not sent prior to our match to the
			// next writer.
			_, err = m.next.Write(p[last:n])
			if err != nil {
				return n, err
			}

			m.matching += min
			n += min
			last = n

			// if we've tracked each byte of our phrase, we can replace it
			if m.matching == len(m.phrase) {
				_, err := m.Write(mask)
				if err != nil {
					return n, err
				}
				m.matching = 0
			}

			continue
		}
```

**File:** common/buildlogger/build_logger.go (L71-74)
```go
	l.maskPhrases = internal.Unique(opts.MaskPhrases)
	l.maskTokenPrefixes = internal.Unique(
		append(opts.MaskTokenPrefixes, tokensanitizer.DefaultTokenPrefixes(opts.MaskAllDefaultTokens)...),
	)
```

**File:** common/buildlogger/build_logger.go (L203-221)
```go
// wrap wraps the underlying writer with "filters". Order here somewhat
// matters, and the order they're instantiated in is the reverse order in which
// writes are processed, e.g. last added filter is the first to process data.
//
// order:
// - sync writer to ensure that multiple writes cannot happen concurrently
// - mask phrases (masker.New)
// - mask sensitive URL parameters (urlsanitizer.New)
// - mask secrets with a prefixed token (tokentanitizer.New)
// - split log lines and add timestamps (timestamper.New)
func (l *Logger) wrap(w io.WriteCloser, streamID int, streamType StreamType) io.WriteCloser {
	if l.timestamping {
		w = timestamper.New(w, timestamper.StreamType(streamType), uint8(streamID), true)
	}

	w = tokensanitizer.New(w, l.maskTokenPrefixes)
	w = urlsanitizer.New(w)
	w = masker.New(w, l.maskPhrases)
	w = internal.NewSync(w)
```

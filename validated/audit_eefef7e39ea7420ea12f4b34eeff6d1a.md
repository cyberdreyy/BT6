### Title
Masker's single-byte-restart heuristic breaks on chunk boundaries for phrases with a repeated-then-different-character prefix, leaking secrets unmasked - ([File: common/buildlogger/internal/masker/masker.go])

### Summary
`masker.Write` at [1](#0-0)  uses a naive "restart at 1 if the failing byte equals `phrase[0]`" heuristic instead of a correct KMP failure function. For phrases whose prefix contains a repeated character run followed by a different character (e.g. `"aaab"`), splitting the input across `Write()` calls at the wrong offsets causes the algorithm to flush already-matched phrase bytes to the downstream writer as "not part of a match" when they actually are part of a real, complete occurrence of the phrase — resulting in the full secret being emitted unmasked into the trace.

### Finding Description
The masker's documented invariant is that "masking [is achieved] over Write() boundaries" via the persistent `matching` field [2](#0-1) . In a single large `Write()` call, on each iteration `min` is computed as the minimum of the *remaining phrase length* and *remaining buffer length* [3](#0-2) , so when the buffer is large the algorithm effectively checks the whole remaining phrase against the buffer at once, correctly rejecting false starts before committing any state.

When `Write()` is called with buffers smaller than the phrase (as happens naturally when job output is streamed a few bytes/line at a time, or forced via a script that flushes byte-by-byte), the algorithm is forced to accumulate `matching` incrementally, one chunk at a time. On a failed extension, it flushes `phrase[:m.matching]` to the next writer and — instead of correctly backtracking to the longest proper border of the already-matched prefix (as true KMP would) — only checks whether the *single failing byte* equals `phrase[0]`, resetting `matching` to `0` or `1` [4](#0-3) .

Concrete trace (phrase `"aaab"`, input `"aaaab"`):
- Single `Write("aaaab")` call: correctly outputs `"a"` + `"[MASKED]"` (byte 0 passthrough, bytes 1-4 masked), verified by hand-trace.
- Same input split into 1-byte or 2-byte chunks (`"aa","aa","b"`, or five 1-byte writes): the algorithm accumulates `matching=2` on `"aa"`, then fails to extend with the next `"aa"` chunk (`phrase[2:4]="ab"` vs the actual next two bytes `"aa"`), flushes the *already-matched* `"aa"` as plaintext, and repeats this pattern for the final byte too — resulting in `"aa"+"aa"+"b"` = the full literal `"aaaab"` reaching the downstream writer completely unmasked.

This directly violates the invariant "phrase masking is chunk-boundary independent" and the core invariant that masked values must not leak into logs/traces. The existing fuzz harness at `build_logger_fuzz.go` [5](#0-4)  already performs random-size chunking and asserts the mask phrase never appears in the sink, but its seed corpus (`"secret"`, `"ssecret"`, `"secrett"`, `"ssecrett"`, and single-character repeats `AAAA…`, `BBBB…`, `CCCC…`) does not include a phrase with this specific "run of repeated char followed by a different char" structure, which is why this bug was never caught: repeated single-char phrases (period 1) never trigger a false partial match that later fails, and `"secret"`-style phrases have no self-overlapping prefix at all, so the naive restart-by-1 heuristic happens to be correct for both cases in the seed set, but not in general.

### Impact Explanation
A masked CI/CD variable (or any masked phrase) whose value happens to contain a short repeated-character run followed by a differing character (a fairly common pattern in real secrets, e.g., tokens with doubled/triple characters, padded values, or any value an attacker can partially predict/construct) can be emitted completely unmasked into the job trace if the job process writes it to stdout/stderr in chunks smaller than the phrase length at the "wrong" offsets. Since a pipeline author fully controls the job script, they can force such small/adversarial chunking (e.g., `printf` per character, disabling stdio buffering, `read -n1` loops) without needing any special privilege beyond normal job authorship, directly defeating the CI/CD variable masking security control and exposing the secret value in the trace, which may be visible to other users with read access to job logs.

### Likelihood Explanation
Preconditions: (1) a masked variable/phrase is accessible to the job (this is the intended use of "masked" CI/CD variables — visible to the job process but expected to be hidden in the trace), (2) the phrase's byte content contains the vulnerable repeated-run pattern, and (3) the job process emits the value in small chunks either naturally (unbuffered writers, byte-oriented tools) or deliberately (attacker-controlled shell script). Precondition 2 is not always attacker-controlled (they generally don't know a value they're trying to leak), but is a plausible, non-contrived pattern for real-world secret formats, and attackers can additionally test/verify this behavior against a masked variable they control (e.g., a project-level masked variable they set themselves with a self-overlapping value) to demonstrate the flaw and infer whether the runner leaks values with this structure — establishing the runner's masking is not chunk-boundary independent, which is the exact defect described in the question. Reproducibility is deterministic once phrase and chunk boundary are known, confirmed via two independent hand traces (1-byte and 2-byte chunking) that both diverge from the correct full-buffer output.

### Recommendation
Replace the ad-hoc single-byte restart heuristic in `masker.Write` with a proper KMP-style failure function (precomputed border/prefix table for `m.phrase`), so that on a failed extension the algorithm backtracks `matching` to the correct longest proper border length of the already-matched prefix, rather than only checking `phrase[0] == p[n]`. This guarantees masking behavior is independent of how the input is chunked across `Write()` calls, matching the doc comment's stated invariant.

### Proof of Concept
Extend `common/buildlogger/internal/build_logger_fuzz.go`'s `Fuzz` (or add a dedicated Go test in the `masker` package) as follows:

```go
func TestMasker_ChunkBoundaryLeak(t *testing.T) {
    phrase := []byte("aaab")
    input := []byte("aaaab")

    // Reference: single full-buffer write must mask correctly.
    var refBuf bytes.Buffer
    refW := masker.New(nopWriteCloser{&refBuf}, [][]byte{phrase})
    refW.Write(input)
    refW.Close()
    if bytes.Contains(refBuf.Bytes(), phrase) {
        t.Fatalf("reference case failed to mask: %q", refBuf.Bytes())
    }

    // Repro: split into 1-byte (and separately 2-byte) writes; must still mask.
    var chunkBuf bytes.Buffer
    chunkW := masker.New(nopWriteCloser{&chunkBuf}, [][]byte{phrase})
    for i := 0; i < len(input); i++ {
        chunkW.Write(input[i : i+1])
    }
    chunkW.Close()

    if bytes.Contains(chunkBuf.Bytes(), phrase) {
        t.Fatalf("secret phrase %q leaked unmasked via byte-at-a-time writes: got %q", phrase, chunkBuf.Bytes())
    }
}
```
Expected: this test currently fails, with `chunkBuf` containing the literal `"aaaab"` (unmasked), while `refBuf` correctly contains `"a[MASKED]"` — demonstrating the chunk-boundary-dependent leak. Additionally, add `[]byte("aaab")`-style phrases (repeated-run-then-differing-char patterns) to the fuzz corpus's `phrases` slice in `build_logger_fuzz.go` so future fuzzing catches this class of input.

### Citations

**File:** common/buildlogger/internal/masker/masker.go (L4-12)
```go
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

**File:** common/buildlogger/internal/masker/masker.go (L81-89)
```go
		// find out how much data we can match: the minimum of len(p) and the
		// remainder of the phrase.
		min := len(m.phrase[m.matching:])
		if len(p[n:]) < min {
			min = len(p[n:])
		}

		// try to match the next part of the phrase
		if bytes.HasPrefix(p[n:], m.phrase[m.matching:m.matching+min]) {
```

**File:** common/buildlogger/internal/masker/masker.go (L113-129)
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
		}
		m.matching = 0
```

**File:** common/buildlogger/internal/build_logger_fuzz.go (L25-103)
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

	tokenPrefixes := [][]byte{
		[]byte("secret_prefix"),
		[]byte("secret-prefix"),
		[]byte("secret_prefix-"),
		[]byte("secret-prefix-"),
		[]byte("secret_prefix_"),
		[]byte("secret-prefix_"),
	}

	// to be combined with tokenPrefixes
	secretSuffixes := [][]byte{
		[]byte("THIS_IS_SECRET"),
		[]byte("ALSO-SECRET"),
	}

	buf := new(bytes.Buffer)

	w := io.WriteCloser(nopWriter{buf})
	w = masker.New(w, phrases)
	w = tokensanitizer.New(w, tokenPrefixes)
	w = urlsanitizer.New(w)

	seed := data
	if len(seed) < 8 {
		seed = append(seed, make([]byte, 8-len(seed))...)
	}
	r := rand.New(rand.NewSource(int64(binary.BigEndian.Uint64(seed))))

	// copy fuzz input to new slice, with interspersed mask values at random locations
	var src []byte
	chunk(r, data, func(part []byte) {
		src = append(src, part...)
		if r.Intn(2) == 1 {
			src = append(src, phrases[r.Intn(len(phrases))]...)
		}
		if r.Intn(2) == 1 {
			pref := tokenPrefixes[r.Intn(len(tokenPrefixes))]
			suf := secretSuffixes[r.Intn(len(secretSuffixes))]
			src = append(src, append(pref, suf...)...)
		}
	})

	// write src to buffer, but with random sized slices
	chunk(r, src, func(part []byte) {
		n, err := w.Write(part)
		if err != nil {
			panic(err)
		}
		if n != len(part) {
			panic(fmt.Sprintf("n(%d) < len(part)(%d)", n, len(part)))
		}
	})

	contents := buf.Bytes()
	for _, mask := range phrases {
		if bytes.Contains(contents, mask) {
			panic(fmt.Sprintf("mask %q present in %q", mask, contents))
		}
	}

	for _, mask := range secretSuffixes {
		if bytes.Contains(contents, mask) {
			panic(fmt.Sprintf("prefix mask %q present in %q", mask, contents))
		}
	}

	return 0
```

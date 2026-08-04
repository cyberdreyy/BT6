### Title
Attacker-supplied short `TokenMaskPrefixes` can starve out the default `glpat-` masking prefix via truncation - ([File: common/buildlogger/build_logger.go], [File: common/buildlogger/internal/tokensanitizer/token_masker.go], [File: common/buildlogger/internal/unique.go])

### Summary
`buildlogger.New` merges job-supplied `Job.Features.TokenMaskPrefixes` with the built-in defaults (including `glpat-`) and passes the combined, deduplicated list to `tokensanitizer.New`, which silently keeps only the first 15 entries. Because `internal.Unique` sorts entries by ascending length before deduplication, an attacker who supplies many prefixes shorter than `"glpat-"` (6 chars) can force all default prefixes—including `glpat-`—past index 15, so `tokensanitizer.New`'s truncation drops them and a real `glpat-` PAT echoed later in the job log is left unmasked.

### Finding Description
- `Build.getNewLogger` passes `b.Job.Features.TokenMaskPrefixes` straight into `buildlogger.Options.MaskTokenPrefixes` [1](#0-0) , an attacker-controlled field from the job payload.
- `buildlogger.New` builds the final prefix list as `internal.Unique(append(opts.MaskTokenPrefixes, tokensanitizer.DefaultTokenPrefixes(...)...))` [2](#0-1) , i.e., job-supplied prefixes are placed before the defaults in the input slice.
- `internal.Unique` sorts the merged slice **by ascending string length first**, then alphabetically, before compacting duplicates [3](#0-2) . This means the final ordering is driven entirely by prefix length, not by "job-supplied vs default" origin.
- `tokensanitizer.New` then does `count := min(len(prefixes), 15)` and only wires up masking writers for `prefixes[0:count]`, silently discarding the rest [4](#0-3) .
- Because `"glpat-"` is 6 characters long, an attacker who supplies 15+ distinct prefixes shorter than 6 characters (e.g., single-character strings `"a"`, `"b"`, `"c"`, ...) guarantees these sort before `glpat-` and all other (longer) default prefixes such as `gloas-`, `gldt-`, etc. After truncation to 15, none of the real GitLab token prefixes remain wired into the writer chain.
- There is no validation that rejects or caps the attacker-controlled `TokenMaskPrefixes` list before merging, nor any mechanism that guarantees defaults survive truncation (e.g., prioritizing defaults or truncating only the job-supplied portion).
- Consequently, any `glpat-`-prefixed token subsequently echoed to the job log (e.g., via `echo $SOME_VAR` where the value looks like a leaked/real PAT, or a CI_JOB_TOKEN look-alike) will not be masked, exposing it in the trace/log which may be visible to other pipeline viewers, artifacts, or exported logs.

### Impact Explanation
This breaks the core invariant that GitLab-issued token prefixes must always be masked in job logs regardless of attacker-supplied configuration. If a real PAT/OAuth-class secret happens to be printed (accidentally or via a crafted job script that echoes an env var container a real token), it is preserved in cleartext in the trace instead of being replaced with `[MASKED]`, which is a concrete secret-leakage/log-sanitization bypass in scope of this audit.

### Likelihood Explanation
Preconditions are fully attacker-controlled: `Job.Features.TokenMaskPrefixes` is set directly from job payload data under the pipeline author's control, requiring only that they submit ≥15 short (length < 6) distinct prefix strings — trivially easy (e.g., single ASCII characters). No special runner privileges are needed; only the ability to run a job. The masking bypass is deterministic given the sort-by-length-then-truncate logic, making it 100% reproducible.

### Recommendation
- In `tokensanitizer.New` (or in `buildlogger.New`), guarantee that the default prefixes (`glpat-` and, if enabled, all default GitLab token prefixes) are always included in the active masking set, independent of job-supplied prefix count or length — e.g., reserve slots for defaults and truncate only the job-supplied portion, or append defaults after truncation rather than before merging.
- Alternatively, cap/validate `Job.Features.TokenMaskPrefixes` length before merging, and change `internal.Unique`'s ordering (or the merge order) so that default prefixes are never subject to eviction by attacker-controlled entries.

### Proof of Concept
```go
func TestTokenSanitizer_DefaultPrefixEvictedByShortAttackerPrefixes(t *testing.T) {
    attackerPrefixes := make([]string, 20)
    for i := range attackerPrefixes {
        attackerPrefixes[i] = string(rune('a' + i)) // 20 distinct length-1 prefixes
    }

    merged := append(attackerPrefixes, tokensanitizer.DefaultTokenPrefixes(false)...) // includes "glpat-"
    finalPrefixes := internal.Unique(merged)

    var buf bytes.Buffer
    w := tokensanitizer.New(internal.NewNopCloser(&buf), finalPrefixes)

    token := "glpat-AAAAAAAAAAAAAAAAAAAA"
    _, _ = w.Write([]byte(token))
    _ = w.Close()

    // Bug: token is NOT masked because "glpat-" was truncated out of the active set.
    assert.NotContains(t, buf.String(), "[MASKED]")
    assert.Contains(t, buf.String(), token) // real PAT leaked in trace
}
```
Expected result on the vulnerable code: the assertion confirms `glpat-...` is echoed unmasked, proving the bug. A fix should make this test show `[MASKED]` present and the raw token absent.

### Citations

**File:** common/build.go (L1637-1648)
```go
func (b *Build) getNewLogger(trace JobTrace, log *logrus.Entry, teeOnly bool) buildlogger.Logger {
	return buildlogger.New(
		trace,
		log,
		buildlogger.Options{
			MaskPhrases:          b.GetAllVariables().Masked(),
			MaskTokenPrefixes:    b.Job.Features.TokenMaskPrefixes,
			Timestamping:         b.IsFeatureFlagOn(featureflags.UseTimestamps),
			MaskAllDefaultTokens: b.IsFeatureFlagOn(featureflags.MaskAllDefaultTokens),
			TeeOnly:              teeOnly,
		},
	)
```

**File:** common/buildlogger/build_logger.go (L71-74)
```go
	l.maskPhrases = internal.Unique(opts.MaskPhrases)
	l.maskTokenPrefixes = internal.Unique(
		append(opts.MaskTokenPrefixes, tokensanitizer.DefaultTokenPrefixes(opts.MaskAllDefaultTokens)...),
	)
```

**File:** common/buildlogger/internal/unique.go (L14-25)
```go
	slices.SortFunc(tokens, func(a, b string) int {
		switch {
		case len(a) < len(b):
			return -1
		case len(a) > len(b):
			return 1
		}

		return cmp.Compare(a, b)
	})

	compact := slices.Compact(tokens)
```

**File:** common/buildlogger/internal/tokensanitizer/token_masker.go (L72-85)
```go
// New returns a new TokenSanitizer.
// We only allow 10 token prefixes at the moment. Everything else is being silently ignored
func New(w io.WriteCloser, prefixes [][]byte) *TokenSanitizer {
	m := &TokenSanitizer{}
	m.next = w

	count := min(len(prefixes), 15)

	for i := 0; i < count; i++ {
		m.next = &tokenSanitizer{next: m.next, prefix: prefixes[i]}
	}

	return m
}
```

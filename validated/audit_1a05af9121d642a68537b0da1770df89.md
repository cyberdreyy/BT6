### Title
Hard-capped 15-prefix limit in `tokensanitizer.New` combined with length-based sorting in `internal.Unique` can silently drop default sensitive token prefixes from masking - ([File: common/buildlogger/internal/tokensanitizer/token_masker.go])

### Summary
`tokensanitizer.New` truncates the prefix list to the first 15 entries via `count := min(len(prefixes), 15)`, silently dropping anything beyond that. Because `buildlogger.New` builds this list by calling `internal.Unique(append(opts.MaskTokenPrefixes, tokensanitizer.DefaultTokenPrefixes(...)...))`, and `Unique` re-sorts the merged list primarily by string length (ascending) before dedup, a sufficiently large number of attacker-influenced short custom prefixes will sort ahead of longer default prefixes (e.g. `glrt-`, `glagent-`, `glsoat-`, `glffct-`, `_gitlab_session=`), pushing them past index 15 where they are never wrapped with a masking writer.

### Finding Description
- `tokensanitizer.New` (common/buildlogger/internal/tokensanitizer/token_masker.go, lines 74-85) hard-caps the number of active masking writers at 15, with the code comment itself acknowledging "Everything else is being silently ignored." [1](#0-0) 
- `buildlogger.New` (common/buildlogger/build_logger.go, lines 68-74) constructs the final prefix list by appending the default prefixes (14 entries when `MaskAllDefaultTokens` is true: `glpat-` plus the 13 in `allTokenPrefixes`, including `glrt-`, `glagent-`, `glsoat-`, `glffct-`, `_gitlab_session=`) after any custom `Options.MaskTokenPrefixes`, then passes the combined list through `internal.Unique`. [2](#0-1) 
- `internal.Unique` does **not** preserve append order. It sorts the merged slice primarily by length (`len(a) < len(b)`) and only falls back to lexical order for equal lengths, then compacts duplicates. [3](#0-2) 
- The result: the 14 default prefixes already occupy nearly all of the 15-slot budget. Because the merge sorts by length, any custom prefixes shorter than the shorter default prefixes (5-char ones like `gldt-`, `glrt-`, `glft-`) will be sorted ahead of them. Supplying as few as 15 short (≤4 character) custom prefixes is enough to push every 5+ character default prefix — including `glrt-` — past index 15 in the sorted list that `tokensanitizer.New` then truncates. Since truncation happens after sorting, the dropped prefixes are silently unmasked, with no error or log signal.
- There is no invariant enforcement anywhere in this pipeline guaranteeing that default/security-relevant prefixes are always included regardless of custom-prefix count; the cutoff operates purely on final sorted position.

### Impact Explanation
If a default sensitive prefix such as `glrt-` (runner registration/reset token) is pushed past the truncation cutoff, any occurrence of that prefixed token in job/trace output (e.g., emitted by helper or checkout logic, error messages, or debug output) would be written to the job log/trace completely unmasked instead of being replaced with `[MASKED]`. Given the shared-runner / multi-tenant nature of GitLab Runner job logs, this is a credential-exposure issue: a runner-scoped or cross-job token that should never appear in plaintext could leak into a job's publicly/CI-visible trace.

### Likelihood Explanation
This requires only that `Options.MaskTokenPrefixes` (attacker/job-influenced custom prefixes) be populated with enough (≥15) short entries to reach `buildlogger.New`. If that field is indeed reachable from job/pipeline-level configuration (as stated in the question's preconditions), triggering the truncation is trivial and fully deterministic — no timing or race conditions are involved, only volume and length of supplied prefixes. I was not able to fully trace, within the available tool budget, the exact code path in `common/build.go` that populates `Options.MaskTokenPrefixes` from job/pipeline input to confirm end-to-end attacker reachability; this precondition should be independently verified before treating the report as fully proven end-to-end. The masking-logic flaw itself (cap + length-sort interaction silently dropping default prefixes), however, is unambiguously present in the code as written.

### Recommendation
- Always mask the default/security-relevant token prefixes unconditionally, independent of any user/custom-supplied prefixes and independent of the 15-slot cap (e.g., reserve fixed slots for defaults and apply the cap only to the custom-prefix portion, or raise/remove the cap and only rely on prefix count for a legitimate performance reason with defaults exempted).
- Do not let `internal.Unique`'s sort order silently determine which prefixes are kept when a hard cap is enforced downstream; if a cap is required, decide truncation based on prefix category (default vs. custom) rather than post-sort position.
- Emit a warning/log (or reject configuration) when the supplied prefix count would cause truncation, so the condition is observable rather than silent.

### Proof of Concept
```go
func TestTokenSanitizer_DefaultPrefixDroppedByTruncation(t *testing.T) {
    // Simulate buildlogger.New's merge: 20 short custom prefixes + all defaults
    custom := make([]string, 20)
    for i := range custom {
        custom[i] = fmt.Sprintf("c%02d", i) // length 3, sorts before 5+ char defaults
    }
    merged := internal.Unique(append(custom, tokensanitizer.DefaultTokenPrefixes(true)...))

    var buf bytes.Buffer
    w := tokensanitizer.New(internal.NewNopCloser(&buf), merged)

    token := "glrt-AAAAAAAAAAAAAAAAAAAA"
    _, _ = w.Write([]byte(token))
    _ = w.Close()

    // Expect masking, but truncation causes it to leak unmasked
    assert.NotContains(t, buf.String(), token, "glrt- token must be masked")
}
```
Expected (buggy) result: the assertion fails — `buf.String()` contains the raw `glrt-...` token because it was sorted past index 15 and dropped from the active `tokenSanitizer` writer chain, demonstrating the truncation bypass.

### Citations

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

**File:** common/buildlogger/build_logger.go (L68-74)
```go
func New(log Trace, entry *logrus.Entry, opts Options) Logger {
	l := Logger{mu: new(sync.Mutex)}

	l.maskPhrases = internal.Unique(opts.MaskPhrases)
	l.maskTokenPrefixes = internal.Unique(
		append(opts.MaskTokenPrefixes, tokensanitizer.DefaultTokenPrefixes(opts.MaskAllDefaultTokens)...),
	)
```

**File:** common/buildlogger/internal/unique.go (L9-35)
```go
func Unique(tokens []string) [][]byte {
	for idx, token := range tokens {
		tokens[idx] = strings.TrimSpace(token)
	}

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
	unique := make([][]byte, 0, len(compact))
	for _, token := range compact {
		if token == "" {
			continue
		}
		unique = append(unique, []byte(token))
	}

	return unique
}
```

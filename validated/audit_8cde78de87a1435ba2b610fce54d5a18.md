Confirmed: `MaskPhrases` are populated as the exact full `Value` of every masked variable via `b.GetAllVariables().Masked()`, and the masker (`common/buildlogger/internal/masker/masker.go`) tracks phrase matches purely as a contiguous byte-run at the `io.Write` level — it has no awareness of "fields" or variable structure. This confirms the mechanism the question describes.

### Title
Masked-secret substrings can leak into logs when a value straddles the `:`/`=` split point in `splitToleration`/`splitMapOverwrite` - (File: `executors/kubernetes/overwrites.go`)

### Summary
`splitToleration` and `splitMapOverwrite` split a user-supplied `KUBERNETES_NODE_TOLERATIONS_<key>` / `KUBERNETES_*_<key>` variable value on `:`/`=` and then log the two resulting fragments together via a single `fmt.Sprintf`/`logger.Println` call that inserts other literal text (`" overwritten with "`, quotes) between them. If a masked secret's plaintext value (referenced by variable expansion into the overwrite value) happens to contain that delimiter internally, the delimiter removal plus insertion of intervening text breaks the secret's byte-contiguity in the emitted log line, so the byte-level masker never sees the full phrase and cannot redact it.

### Finding Description
`createOverwrites` expands job variables (`variables = variables.Expand()` at [1](#0-0) ) and feeds `KUBERNETES_NODE_TOLERATIONS_<key>`/`KUBERNETES_*_<key>` values into `evaluateMapOverwrite`, which calls the provided `split` function (`splitToleration` or `splitMapOverwrite`) and then logs the resulting `key`/`value` pair on a single line: [2](#0-1) .

`splitToleration` splits on the first `:` with `strings.SplitN(toleration, ":", 2)` [3](#0-2) , and `splitMapOverwrite` splits on the first `=` [4](#0-3) . Neither preserves the original contiguous string when logged — the log line reconstructs the two fragments with `fieldName`/quotes/`"overwritten with"` text spliced between them.

The masking pipeline operates strictly at the byte-stream level: `MaskPhrases` are the literal, full `Value` of every job variable marked `Masked: true` (`b.GetAllVariables().Masked()`) [5](#0-4) [6](#0-5) , and the `masker` writer matches these phrases only as an unbroken run of bytes across `Write()` calls [7](#0-6) . It has no concept of the original variable/field boundaries; it only sees the final formatted log text.

Exploit flow: a pipeline author (with `KUBERNETES_NODE_TOLERATIONS_OVERWRITE_ALLOWED`/equivalent overwrite regex enabled by an admin, which is the intended legitimate use of this feature) sets e.g. `KUBERNETES_NODE_TOLERATIONS_foo: "key=$SOME_MASKED_VAR"`. If `SOME_MASKED_VAR`'s value contains a `:` internally (common for credential-style secrets such as `user:pass`, `SHA256:fingerprint`, `host:port` connection strings), `splitToleration` will split the secret's plaintext in half at that internal `:`. The log line `"NodeTolerations" "key=<part-before-colon>" overwritten with "<part-after-colon>"` no longer contains the secret as one contiguous byte run, so the byte-oriented masker fails to match it, and both halves of the secret are printed in clear text in the job log/trace.

Existing protections do not stop this: `overwriteRegexCheck` only validates the overwrite value against an admin-configured allow-regex (format validation, not content/secrecy) at [8](#0-7) , and masking coverage is asserted only for the literal, unmodified phrase — there is no re-check that transformed/reformatted log output still fully contains the phrase.

### Impact Explanation
A masked CI/CD variable's plaintext value can leak into the job log/trace when that value is referenced (via variable expansion) inside a `KUBERNETES_NODE_TOLERATIONS_*`/`KUBERNETES_*_LABELS_*`/`KUBERNETES_NODE_SELECTOR_*` override and happens to contain the split delimiter (`:` or `=`) internally. This directly violates the core invariant that masked/secret values must not leak into logs or traces, and is exploitable by an ordinary pipeline author once the corresponding overwrite feature is enabled (a normal, documented admin configuration, not a misconfiguration being excused).

### Likelihood Explanation
Preconditions: (1) the relevant `*OverwriteAllowed` regex must be non-empty (admin-enabled, standard feature usage), (2) a masked variable is referenced inside an override variable's value, and (3) that masked variable's value contains `:` (for tolerations) or `=` (for labels/annotations/selectors) at some internal position. Colon- or equals-containing secrets are common (basic-auth style credentials, connection strings, base64 padding). This is deterministic and repeatable — no timing/race is needed, and it reproduces on every job run with such inputs.

### Recommendation
After building the log line for `evaluateMapOverwrite`/`splitToleration`/`splitMapOverwrite`, verify that any masked phrase which was present in the original (pre-split) variable value is still masked in the final formatted output, or simpler: log the original, unsplit `variable.Value` as a single contiguous string (so the existing masker can match it intact) rather than reconstructing it with intervening literal text after splitting.

### Proof of Concept
Go unit test in `executors/kubernetes/overwrites_test.go` style, combined with `common/buildlogger`:
```go
func TestTolerationSplitBreaksMasking(t *testing.T) {
    secret := "user:pass123"           // masked variable value, contains ':'
    tolerationValue := "key=" + secret // KUBERNETES_NODE_TOLERATIONS_foo value

    var buf bytes.Buffer
    logger := buildlogger.New(&fakeTrace{&buf}, nil, buildlogger.Options{
        MaskPhrases: []string{secret},
    })

    key, effect, err := splitToleration(tolerationValue)
    require.NoError(t, err)
    logger.Println(fmt.Sprintf("%q %q overwritten with %q", "NodeTolerations", key, effect))
    require.NoError(t, logger.Close())

    // Expect the secret to be fully masked; currently it is NOT,
    // because the ':' split breaks byte-contiguity of the phrase.
    assert.NotContains(t, buf.String(), "user")
    assert.NotContains(t, buf.String(), "pass123")
}
```
Expected today: the assertions fail — `"user"` (before the split) and/or `"pass123"` (after) appear unmasked in `buf.String()`, confirming the bypass.

### Citations

**File:** executors/kubernetes/overwrites.go (L155-155)
```go
	variables = variables.Expand()
```

**File:** executors/kubernetes/overwrites.go (L620-630)
```go
func overwriteRegexCheck(regex, value string) error {
	var err error
	var r *regexp.Regexp
	if r, err = regexp.Compile(regex); err != nil {
		return err
	}
	if match := r.MatchString(value); !match {
		return &malformedOverwriteError{value: value, pattern: regex}
	}
	return nil
}
```

**File:** executors/kubernetes/overwrites.go (L634-640)
```go
func splitMapOverwrite(str string) (string, string, error) {
	if split := strings.SplitN(str, "=", 2); len(split) > 1 {
		return split[0], split[1], nil
	}

	return "", "", &malformedOverwriteError{value: str, pattern: "k=v"}
}
```

**File:** executors/kubernetes/overwrites.go (L648-657)
```go
func splitToleration(toleration string) (string, string, error) {
	effect := ""
	colonParts := strings.SplitN(toleration, ":", 2)
	if len(colonParts) > 1 {
		effect = colonParts[1]
	}
	keyvalue := colonParts[0]

	return keyvalue, effect, nil
}
```

**File:** executors/kubernetes/overwrites.go (L687-693)
```go
		key, value, err := split(variable.Value)
		if err != nil {
			return nil, err
		}

		finalValues[key] = value
		logger.Println(fmt.Sprintf("%q %q overwritten with %q", fieldName, key, value))
```

**File:** common/build.go (L1642-1642)
```go
			MaskPhrases:          b.GetAllVariables().Masked(),
```

**File:** common/spec/variables.go (L169-176)
```go
func (b Variables) Masked() (masked []string) {
	for _, variable := range b {
		if variable.Masked {
			masked = append(masked, variable.Value)
		}
	}
	return
}
```

**File:** common/buildlogger/internal/masker/masker.go (L55-90)
```go
func (m *masker) Write(p []byte) (n int, err error) {
	if len(p) == 0 {
		return 0, nil
	}

	// fast path: if the write is "[MASKED]" from an upper-level, don't bother
	// processing it, send it to the next writer.
	if bytes.Equal(p, mask) {
		return m.next.Write(p)
	}

	var last int
	for n < len(p) {
		// optimization: use the faster IndexByte to jump to the start of a
		// potential phrase and if not found, advance the whole buffer.
		if m.matching == 0 {
			off := bytes.IndexByte(p[n:], m.phrase[0])
			if off < 0 {
				n += len(p[n:])
				break
			}
			if off > -1 {
				n += off
			}
		}

		// find out how much data we can match: the minimum of len(p) and the
		// remainder of the phrase.
		min := len(m.phrase[m.matching:])
		if len(p[n:]) < min {
			min = len(p[n:])
		}

		// try to match the next part of the phrase
		if bytes.HasPrefix(p[n:], m.phrase[m.matching:m.matching+min]) {
			// send any data that we've not sent prior to our match to the
```

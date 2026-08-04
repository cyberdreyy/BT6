### Title
Streaming masker leaks unmasked prefix of secret when phrase match is incomplete at `Close()` - ([File: common/buildlogger/internal/masker/masker.go])

### Summary
`masker.(*masker).Close` (lines 141-159) flushes `m.phrase[:m.matching]` verbatim to the next writer whenever a tracked partial match (`0 < m.matching < len(m.phrase)`) has not completed by the time the stream closes. [1](#0-0)  Since `m.matching` bytes withheld in the masker are byte-for-byte identical to the corresponding prefix of the actual secret phrase, any job whose last relevant output is a genuine partial-length echo of a masked variable (e.g. `printf '%s' "${SECRET:0:N}"` with `N < len(SECRET)`) causes that exact prefix to be written to the trace unmasked.

### Finding Description
`buildlogger.wrap` chains a `masker.New` writer into every job's stdout pipeline using `l.maskPhrases` built from `b.GetAllVariables().Masked()`, so any variable a job author flags/receives as masked (including protected/masked custom variables and default tokens) is a tracked phrase. [2](#0-1) [3](#0-2) 

In `masker.Write`, when incoming bytes match `m.phrase` starting at offset 0, the matched bytes are withheld from `m.next` (not yet written to the trace) and tracked via `m.matching` until either the full phrase completes (triggering replacement with `[MASKED]`) or a subsequent byte diverges (triggering an unmasked flush of the withheld bytes at lines 113-128). [4](#0-3)  If the stream simply ends — i.e., the job's last output for that variable is a genuine prefix shorter than the full secret, and no more data ever arrives — `Write` never resolves the pending match, and `Close` is invoked instead. `Close` explicitly flushes `m.phrase[:m.matching]`, the actual withheld secret-prefix bytes, to `m.next` unmasked. [5](#0-4) 

Because a job process legitimately has the real value of masked/protected variables in its environment (masking hides the value from *log viewers*, not from the running job), a job author can deterministically produce this state without any race or timing trick — the last command of the job simply prints `${VAR:0:N}` for any `N` up to `len(VAR)-1`, and the job completes normally, which always triggers `Logger.Close()` -> masker `Close()` at end of build. This can leak up to `len(phrase)-1` characters of the secret in a single job run — effectively the entire value except the final character.

No existing check mitigates this: the `Write()`-path divergence flush (lines 113-128) has the identical unmasked-flush behavior, and the fuzz harness (`common/buildlogger/internal/build_logger_fuzz.go`) never calls `Close()` on the writer chain, so this Close-triggered disclosure path is untested. The unit tests in `masker_test.go` also always feed complete phrases, never a genuinely truncated tail followed by `Close()`. [6](#0-5) [7](#0-6) 

### Impact Explanation
An unprivileged pipeline author can force partial (up to `len(secret)-1` bytes) disclosure of any masked CI/CD variable — including protected/masked variables they are not supposed to be able to exfiltrate to viewers of the job log/trace, and `CI_JOB_TOKEN` — by echoing a substring prefix of the variable as the job's terminal output. This defeats the confidentiality goal of variable masking for any viewer with only log-read access.

### Likelihood Explanation
Fully deterministic and repeatable: no race condition, network interruption, or cancellation timing is required. A job author only needs shell string-slicing (`${VAR:0:N}`) and normal job completion. This is trivially reproducible in any executor that supports a shell.

### Recommendation
`Close()` should not flush withheld matched bytes verbatim when `0 < m.matching < len(m.phrase)`; at minimum this partial-match tail should be masked (e.g. emit `[MASKED]` for any matched prefix length above a safe threshold, or track and redact suffix fragments similarly to how `tokensanitizer.Close` already redacts its `m.masked` state) rather than exposing raw phrase bytes at end-of-stream.

### Proof of Concept
```go
// common/buildlogger/internal/masker/masker_close_test.go
func TestPartialPhraseLeakOnClose(t *testing.T) {
    secret := "supersecrettoken1234"
    buf := new(bytes.Buffer)
    m := New(internal.NewNopCloser(buf), internal.Unique([]string{secret}))

    // Simulate job echoing "${SECRET:0:N}" as its last output, N < len(secret)
    n, err := m.Write([]byte(secret[:len(secret)-1]))
    require.NoError(t, err)
    require.Equal(t, len(secret)-1, n)

    require.NoError(t, m.Close())

    // Assert: no prefix of length >= 4 of the real secret should ever
    // appear unmasked in the trace output.
    out := buf.String()
    assert.NotContains(t, out, secret[:4], "partial secret prefix leaked via Close()")
    assert.NotContains(t, out, secret[:len(secret)-1], "near-complete secret leaked via Close()")
}
```
Expected current behavior: the test fails because `buf.String()` equals `secret[:len(secret)-1]` verbatim, proving the leak. Contrast with the full-match case (`m.Write([]byte(secret))`) which correctly outputs `[MASKED]`.

### Citations

**File:** common/buildlogger/internal/masker/masker.go (L88-128)
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
```

**File:** common/buildlogger/internal/masker/masker.go (L141-151)
```go
func (m *masker) Close() error {
	var werr error

	if m.matching == len(m.phrase) {
		// this mask is added to avoid a potential undiscovered edge-case:
		// this should be unreachable as we replace full matches immediately in
		// Write().
		_, werr = m.next.Write(mask)
	} else {
		_, werr = m.next.Write(m.phrase[:m.matching])
	}
```

**File:** common/build.go (L1642-1642)
```go
			MaskPhrases:          b.GetAllVariables().Masked(),
```

**File:** common/buildlogger/build_logger.go (L213-224)
```go
func (l *Logger) wrap(w io.WriteCloser, streamID int, streamType StreamType) io.WriteCloser {
	if l.timestamping {
		w = timestamper.New(w, timestamper.StreamType(streamType), uint8(streamID), true)
	}

	w = tokensanitizer.New(w, l.maskTokenPrefixes)
	w = urlsanitizer.New(w)
	w = masker.New(w, l.maskPhrases)
	w = internal.NewSync(w)

	return w
}
```

**File:** common/buildlogger/internal/masker/masker_test.go (L16-131)
```go
func TestMasking(t *testing.T) {
	tests := []struct {
		input    string
		values   []string
		expected string
	}{
		{
			input:    "empty secrets have no affect",
			values:   []string{""},
			expected: "empty secrets have no affect",
		},
		{
			input:    "no escaping at all",
			expected: "no escaping at all",
		},
		{
			input:    "secrets",
			values:   []string{"secrets"},
			expected: "[MASKED]",
		},
		{
			input:    "secret|s",
			values:   []string{"secrets"},
			expected: "[MASKED]",
		},
		{
			input:    "s|ecrets",
			values:   []string{"secrets"},
			expected: "[MASKED]",
		},
		{
			input:    "secretssecrets",
			values:   []string{"secrets"},
			expected: "[MASKED][MASKED]",
		},
		{
			input:    "ssecrets",
			values:   []string{"secrets"},
			expected: "s[MASKED]",
		},
		{
			input:    "s|secrets",
			values:   []string{"secrets"},
			expected: "s[MASKED]",
		},
		{
			input:    "at the start of the buffer",
			values:   []string{"at"},
			expected: "[MASKED] the start of the buffer",
		},
		{
			input:    "in the middle of the buffer",
			values:   []string{"middle"},
			expected: "in the [MASKED] of the buffer",
		},
		{
			input:    "at the end of the buffer",
			values:   []string{"buffer"},
			expected: "at the end of the [MASKED]",
		},
		{
			input:    "all values are masked",
			values:   []string{"all", "values", "are", "masked"},
			expected: "[MASKED] [MASKED] [MASKED] [MASKED]",
		},
		{
			input:    "prefixed and suffixed: xfoox ybary ffoo barr ffooo bbarr",
			values:   []string{"foo", "bar"},
			expected: "prefixed and suffixed: x[MASKED]x y[MASKED]y f[MASKED] [MASKED]r f[MASKED]o b[MASKED]r",
		},
		{
			input:    "prefix|ed, su|ffi|xed |and split|:| xfo|ox y|bary ffo|o ba|rr ffooo b|barr",
			values:   []string{"foo", "bar"},
			expected: "prefixed, suffixed and split: x[MASKED]x y[MASKED]y f[MASKED] [MASKED]r f[MASKED]o b[MASKED]r",
		},
		{
			input:    "sp|lit al|l val|ues ar|e |mask|ed",
			values:   []string{"split", "all", "values", "are", "masked"},
			expected: "[MASKED] [MASKED] [MASKED] [MASKED] [MASKED]",
		},
		{
			input:    "prefix_mask mask prefix_|mask prefix_ma|sk mas|k",
			values:   []string{"mask", "prefix_mask"},
			expected: "[MASKED] [MASKED] [MASKED] [MASKED] [MASKED]",
		},
		{
			input:    "large secret: " + strings.Repeat("_", 8000) + "|" + strings.Repeat("_", 8000),
			values:   []string{strings.Repeat("_", 8000*2)},
			expected: "large secret: [MASKED]",
		},
		{
			input:    "overlap: this is the en| foobar",
			values:   []string{"this is the end", "en foobar", "en"},
			expected: "overlap: this is the [MASKED]",
		},
	}

	for _, tc := range tests {
		t.Run(tc.input, func(t *testing.T) {
			buf := new(bytes.Buffer)

			m := New(internal.NewNopCloser(buf), internal.Unique(tc.values))

			parts := bytes.Split([]byte(tc.input), []byte{'|'})
			for _, part := range parts {
				n, err := m.Write(part)
				require.NoError(t, err)

				assert.Equal(t, len(part), n)
			}

			require.NoError(t, m.Close())
			assert.Equal(t, tc.expected, buf.String())
		})
	}
}
```

**File:** common/buildlogger/internal/build_logger_fuzz.go (L79-88)
```go
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
```

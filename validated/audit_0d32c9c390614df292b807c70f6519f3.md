This confirms the exploit premise is invalid.

### Analysis

The tarzstd `Archive()` function does construct an error using `fmt.Errorf("%s cannot be archived from outside of chroot (%s)", name, a.dir)` with the raw, attacker-controlled filename embedded verbatim, as claimed [1](#0-0) . This is called from `CacheArchiverCommand.createZipFile` via `archiver.Archive(...)`, and on error, `Execute()` calls `logrus.Fatalln(err)` [2](#0-1) , which writes to the helper process's stderr.

However, the `cache-archiver` helper is invoked as a subprocess from within the generated shell script executed by the job's shell/executor process. That process's stdout/stderr streams are wired to `s.BuildLogger.Stream(...)`, e.g. in the shell executor: `cmdOpts.Stdout = stdout; cmdOpts.Stderr = stderr` where `stdout`/`stderr` come from `s.BuildLogger.Stream(...)` [3](#0-2) . `Logger.Stream()` returns `l.wrap(l.base, streamID, streamType)` [4](#0-3) , and `wrap()` always chains `tokensanitizer.New` → `urlsanitizer.New` → `masker.New` → `internal.NewSync` before the underlying trace writer [5](#0-4) .

This means **any** byte stream that ends up as stdout/stderr of a helper process invoked during job execution — including this `cache-archiver` chroot-violation error message — passes through the exact same `masker.Write` → `tokensanitizer.Write` chain as ordinary `echo $SECRET` job output, before reaching the trace. There is no separate/alternate code path from CLI helper output to the trace that skips `wrap()`; the masking is applied at the point where bytes are written into the job log stream, independent of which process or code path produced those bytes. Consequently, if the attacker's filename equals a value registered in `Options.MaskPhrases` (i.e., it corresponds to an actual masked CI/CD variable value), `masker.Write`'s byte-scanning logic [6](#0-5)  would still detect and replace the phrase with `[MASKED]` in the error text exactly as it would for any other occurrence of that phrase in job output.

The premise that this error path "bypasses masker.Write/tokensanitizer.Write" is therefore not supported by the code — the chroot-violation error string is subject to the same masking pipeline as all other job trace output, because it's just bytes on a stream that's captured and piped through `BuildLogger`'s `wrap()`-wrapped writer.

#No vulnerability found for this question.

### Citations

**File:** commands/helpers/archive/tarzstd/tarzstd_archiver.go (L72-74)
```go
		if !strings.HasPrefix(path, a.dir+string(filepath.Separator)) && path != a.dir {
			return fmt.Errorf("%s cannot be archived from outside of chroot (%s)", name, a.dir)
		}
```

**File:** commands/helpers/cache_archiver.go (L325-333)
```go
	size, err := c.createZipFile(c.File)
	if err != nil {
		logrus.Fatalln(err)
	}

	err = writeCacheMetadataFile(c.File, c.Metadata)
	if err != nil {
		logrus.Fatalln(err)
	}
```

**File:** executors/shell/shell.go (L75-87)
```go
	stdout := s.BuildLogger.Stream(buildlogger.StreamWorkLevel, buildlogger.Stdout)
	defer stdout.Close()

	stderr := s.BuildLogger.Stream(buildlogger.StreamWorkLevel, buildlogger.Stderr)
	defer stderr.Close()

	cmdOpts := process.CommandOptions{
		Env:                             os.Environ(),
		Stdout:                          stdout,
		Stderr:                          stderr,
		UseWindowsLegacyProcessStrategy: s.Build.IsFeatureFlagOn(featureflags.UseWindowsLegacyProcessStrategy),
		UseWindowsJobObject:             s.Build.IsFeatureFlagOn(featureflags.UseWindowsJobObject),
	}
```

**File:** common/buildlogger/build_logger.go (L90-99)
```go
func (l *Logger) Stream(streamID int, streamType StreamType) io.WriteCloser {
	// l.base being nil happens when the buildlogger hasn't been created with New() or
	// a nil was passed for the Trace parameter. This only happens in tests, and to not
	// panic we simply return a discard writer.
	if l.base == nil {
		return internal.NewNopCloser(io.Discard)
	}

	return l.wrap(l.base, streamID, streamType)
}
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

**File:** common/buildlogger/internal/masker/masker.go (L55-138)
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
		m.matching = 0

		n++
	}

	// any unmatched data is sent to the next writer
	_, err = m.next.Write(p[last:n])

	return n, err
}
```

### Title
Forgeable pause/resume markers in `entrypointLogForwarder.writeLine` allow attacker-controlled entrypoint output to prematurely resume log forwarding - (File: executors/kubernetes/container_entrypoint_forwarder.go)

### Summary
`entrypointLogForwarder.writeLine` decides whether to pause or resume forwarding of container entrypoint log lines purely by pattern-matching arbitrary JSON content (`{"script":...}` / `{"command_exit_code":...}`) that appears after a timestamp prefix, with no cryptographic or structural proof that the marker originated from the runner's own trap-instrumented wrapper script. Since the monitored stream is the stdout of the container's actual entrypoint — which is determined by the job's `image:`/`entrypoint:` configuration and is therefore attacker-influenceable — a crafted line that merely resembles the expected JSON schema can force `lf.paused` to flip state out of sequence with the real script execution.

### Finding Description
`writeLine` calls `commandStatus(p)` [1](#0-0) , which only checks that the line begins with something parseable as an RFC3339Nano-like timestamp followed by a space and JSON matching `stageCommandExitStatusImpl` [2](#0-1)  and [3](#0-2) . There is no shared secret, sequence counter, or out-of-band channel tying a given marker to the specific runner-injected trap invocation — any writer of a line into the same log stream that happens to match the pattern `{"command_exit_code":N}` or `{"script":"name"}` after a real timestamp will be treated as authoritative.

The timestamp itself provides no authentication: it is injected by the container runtime/Kubernetes log API (`--timestamps`), not the runner, and is applied uniformly to every line written to the container's stdout regardless of the writing process. Consequently, any line the container's own entrypoint chooses to `echo` will be timestamped identically to a genuine trap marker, and `commandStatus` cannot distinguish between the two.

This matches the described exploit sequence: if the attacker's entrypoint writes a line that JSON-decodes to `{"command_exit_code":0}` while `lf.paused == true`, `writeLine` sets `lf.paused = false` [4](#0-3) , and every subsequent line the entrypoint writes (until the next real/forged `script` marker) is forwarded to `Sink`/trace [5](#0-4) .

### Impact Explanation
Whether this yields a concrete secret leak depends on whether the runner ever writes sensitive data (env dumps, injected CI variables) to the same stream during the window that `paused` is expected to remain `true`. The code itself provides no protection against out-of-order or forged pause/resume transitions — the invariant stated in the question ("pause/resume state driven only by legitimate runner-injected markers, not forgeable by job-controlled entrypoint output") does not hold in the current implementation, since the only gate is a JSON-shape check on untrusted stream content. If any suppressed diagnostic output containing sensitive values is ever written to this same stdout stream during a paused interval, this flaw allows a job-controlled entrypoint (settable via the job's custom `image:`/`entrypoint:`/`command:` in `.gitlab-ci.yml`) to force it into the trace early.

### Likelihood Explanation
Exploitability requires only that a pipeline author can control the entrypoint/command of the image used for the job (a standard, always-available capability), and that this entrypoint's stdout is exactly the stream fed into `entrypointLogForwarder`. Forging the marker is trivial — a single `echo '{"command_exit_code":0}'` from the entrypoint script. However, I could not confirm from the available files whether the specific "internal/protected diagnostic output" alluded to in the question (env dumps with secrets) is actually written into this same stream during the paused window, versus being carried through a separate, already-isolated channel (e.g., a distinct attach/exec stream per build stage). This is the main uncertainty preventing a fully concrete impact assessment; the index available to me does not include the wiring code in `executors/kubernetes/kubernetes.go` that constructs and feeds this forwarder, so the exact contents of the "entrypoint" stream during the paused interval could not be verified.

### Recommendation
Replace content-based pause/resume detection with an authenticated/structural mechanism: e.g., write markers to a dedicated, non-job-writable file descriptor or named pipe rather than commingling them with the monitored process's own stdout, or prefix markers with a runner-generated per-job random token that is never disclosed to the job/image so it cannot be replicated by job-controlled output.

### Proof of Concept
```go
func TestEntrypointLogForwarder_ForgedResumeMarker(t *testing.T) {
    sink := &fakeEntrypointForwarderSink{}
    lf := &entrypointLogForwarder{Sink: sink}

    var buf timestampBuffer
    // genuine pause marker
    fmt.Fprintln(&buf, `{"script": "step_script"}`)
    // attacker-controlled entrypoint output forging an "exited" line
    fmt.Fprintln(&buf, `{"command_exit_code": 0}`)
    // sensitive-looking line that should have remained suppressed
    fmt.Fprintln(&buf, "SECRET_TOKEN=abc123")

    _, err := io.Copy(lf, &buf)
    require.NoError(t, err)

    // BUG: forged marker causes premature resume, so the secret-looking
    // line ends up in sink/trace even though it should still be paused.
    assert.Contains(t, sink.String(), "SECRET_TOKEN=abc123")
}
```
This test demonstrates that a line matching the `StageCommandStatus` JSON schema — indistinguishable in structure from a real trap marker — flips `paused` back to `false` and allows subsequent lines to reach the `Sink`, confirming the forgeability of the pause/resume state machine.

### Citations

**File:** executors/kubernetes/container_entrypoint_forwarder.go (L22-30)
```go
func (lf *entrypointLogForwarder) writeLine(p []byte) error {
	cmdStatus, ok := lf.commandStatus(p)
	if ok {
		if cmdStatus.IsExited() {
			lf.paused = false
		} else if cmdStatus.BuildStage() != "" {
			lf.paused = true
		}
	}
```

**File:** executors/kubernetes/container_entrypoint_forwarder.go (L32-38)
```go
	if lf.paused || ok {
		return nil
	}

	_, err := lf.Sink.Write(p)
	return err
}
```

**File:** executors/kubernetes/container_entrypoint_forwarder.go (L91-113)
```go
func (lf *entrypointLogForwarder) commandStatus(p []byte) (shells.StageCommandStatus, bool) {
	cmdStatus := shells.StageCommandStatus{}

	// check if the first part resembles a timestamp
	if len(p) < len(containerLoggerTimeStampFormat) ||
		p[len(containerLoggerTimeStampFormat)] != ' ' {
		return cmdStatus, false
	}

	line := string(p)
	ts := line[:len(containerLoggerTimeStampFormat)]
	_, err := time.Parse(containerLoggerTimeStampFormat, ts)

	if err != nil {
		return cmdStatus, false
	}

	// the actual log line starts after the timestamp + a space
	line = line[len(containerLoggerTimeStampFormat)+1:]

	ok := cmdStatus.TryUnmarshal(line)
	return cmdStatus, ok
}
```

**File:** shells/trap_command_exit_status.go (L44-61)
```go
// TryUnmarshal tries to unmarshal a json string into its pointer receiver.
// It wil return true only if the unmarshalled struct has all of its required fields be non-nil.
// It's safe to use the struct only if this method returns true.
func (c *StageCommandStatus) TryUnmarshal(line string) bool {
	var status stageCommandExitStatusImpl
	err := status.tryUnmarshal(line)
	if err != nil {
		return false
	}

	if status.isEmpty() {
		return false
	}

	status.applyTo(c)

	return true
}
```

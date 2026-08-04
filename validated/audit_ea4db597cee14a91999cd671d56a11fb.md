[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** executors/kubernetes/container_entrypoint_forwarder.go (L1-20)
```go
package kubernetes

import (
	"io"
	"time"

	"gitlab.com/gitlab-org/gitlab-runner/shells"
)

const containerLoggerTimeStampFormat = "2006-01-02T15:04:05.999999999Z"

// entrypointLogForwarder implements an io.WriteCloser and forwards logs to the Sink.
// If we see markers for starting or stopping a step, we pause / resume log forwarding, so that we only forward logs
// that are not captured through other means.
type entrypointLogForwarder struct {
	Sink io.WriteCloser

	buffer []byte
	paused bool
}
```

**File:** executors/kubernetes/container_entrypoint_forwarder.go (L22-38)
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

	if lf.paused || ok {
		return nil
	}

	_, err := lf.Sink.Write(p)
	return err
}
```

**File:** executors/kubernetes/container_entrypoint_forwarder.go (L89-113)
```go
// commandStatus inspects the current data if it's a [shells.StageCommandStatus]
// This is done, so we understand if the logs coming in are part of a step_command or "something else".
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

Based on the investigation, I found a legitimate unprivileged-actor analog in the Chainlink node's async bridge task / pipeline resume flow.

### Title
Pipeline run resumption authenticated only by a leaked/guessable UUID enables unauthenticated result injection - (File: core/web/pipeline_runs_controller.go)

### Summary
The external report's core bug class is that a supposedly protective extra step (the "claim" mechanic) is unauthenticated/misdesigned, allowing state to be manipulated by parties who should not be trusted, with no way to recover from misuse. The Chainlink node has an analogous pattern in its async bridge/external-adapter callback flow: pipeline runs suspended by an async `BridgeTask` are resumed via `PATCH /v2/resume/:runID`, and the *only* credential protecting this state transition is the task UUID embedded in a URL, with no session, API token, or HMAC verification at the handler level.

### Finding Description
When a job pipeline runs a `BridgeTask` with `Async: "true"`, the node builds a `responseURL` containing the task's UUID and sends it to the external adapter (EA) as the place to POST the eventual result: [1](#0-0) 

The node then suspends the run (`RunInfo{IsPending: true}`) and waits for a callback to that URL: [2](#0-1) 

The corresponding controller for that callback, `PipelineRunsController.Resume`, parses `taskID` straight from the URL parameter and calls `App.ResumeJobV2` with an attacker/caller-supplied `pipeline.Result` value/error — there is no session or API-key check inside the handler itself, and the audit event is explicitly named `UnauthedRunResumed`: [3](#0-2) 

`ResumeJobV2` forwards directly into the pipeline runner: [4](#0-3) 

`runner.ResumeRun` unconditionally updates the task run's stored `Result` for the given `taskID` and restarts the pipeline with attacker-controlled `value`/`err`: [5](#0-4) 

The only thing standing between "legitimate EA response" and "attacker-forged response" is possession of the task's UUID — a capability token that is transmitted in plaintext as a URL path segment to third-party EA services (over HTTP, per `bridge.URL` configuration), and is not bound to the caller's identity, IP, or any secondary secret. Unlike the external initiator flow, which authenticates via `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` compared with `subtle.ConstantTimeCompare` [6](#0-5) , the resume endpoint has no equivalent secret-comparison step protecting the state transition — this mirrors the report's core defect: an extra "claim"-like step (waiting for/consuming a callback) that is trusted to gate a sensitive state change without adequate protection, and once bypassed there is no way to distinguish or roll back the forged result.

### Impact Explanation
Any actor who obtains or guesses a pending task's UUID (e.g., via logging by the EA, browser history, proxy logs, network capture on non-TLS bridge endpoints, or misconfigured EA infrastructure) can inject an arbitrary result/error into a suspended pipeline run. Because pipeline outputs commonly feed downstream tasks (e.g., `median`, EVM transaction submission via `TxManager().RegisterResumeCallback(pipelineRunner.ResumeRun)` [7](#0-6) ), a forged resume can corrupt price/VRF/data results that are ultimately submitted on-chain, potentially leading to unauthorized job outcomes or funds movement stemming from bad data.

### Likelihood Explanation
Exploitation requires the attacker to learn the specific UUID used as the resume token for a pending run, since the endpoint is not rate-limited or bound to caller identity in the handler shown. This is a real but bounded exposure vector (log leakage, EA misconfiguration, non-HTTPS bridge URLs, shared/compromised EA infra) rather than a trivially-guessable value, so likelihood is moderate rather than high.

### Recommendation
Bind the resume callback to a stronger, non-guessable, single-use, and ideally HMAC-signed token that is verified against a stored secret at resume time (similar to the constant-time secret comparison already used for `ExternalInitiator` auth), rather than relying solely on possession of the URL-embedded UUID. Additionally, mark the resume token as consumed after first use and log/alert on repeated or out-of-order resume attempts for the same `taskID`.

### Proof of Concept
1. Configure a job with an async `BridgeTask` pointing to an external adapter reachable by an unprivileged network observer (e.g., HTTP bridge URL, or an EA whose logs are exposed).
2. Trigger the job; the node suspends the run and sends the EA a request containing `responseURL: http://<node>/v2/resume/<taskID>` (`task.bridge.go` lines 363-373).
3. Before the legitimate EA responds, an attacker who has observed `<taskID>` sends `PATCH /v2/resume/<taskID>` with an arbitrary JSON body, e.g. `{"data":"forgedResult"}`.
4. `PipelineRunsController.Resume` accepts the request without further authentication and calls `ResumeJobV2` → `runner.ResumeRun`, which writes the forged value into the task run and resumes/finalizes the pipeline with attacker-controlled data.

### Citations

**File:** core/services/pipeline/task.bridge.go (L299-311)
```go
	if t.Async == "true" {
		// Look for a `pending` flag. This check is case-insensitive because http.Header normalizes header names
		if _, ok := headers["X-Chainlink-Pending"]; ok {
			return result, pendingRunInfo()
		}

		var response struct {
			Pending bool `json:"pending"`
		}
		if err := json.Unmarshal(responseBytes, &response); err == nil && response.Pending {
			return Result{}, pendingRunInfo()
		}
	}
```

**File:** core/services/pipeline/task.bridge.go (L363-373)
```go
	if t.Async == "true" {
		responseURL := t.bridgeConfig.BridgeResponseURL()
		if responseURL != nil && *responseURL != *zeroURL {
			responseURL.Path = path.Join(responseURL.Path, "/v2/resume/", t.uuid.String())
		}
		var s string
		if responseURL != nil {
			s = responseURL.String()
		}
		merged["responseURL"] = s
	}
```

**File:** core/web/pipeline_runs_controller.go (L130-159)
```go
// Resume finishes a task and resumes the pipeline run.
// Example:
// "PATCH <application>/jobs/:ID/runs/:runID"
func (prc *PipelineRunsController) Resume(c *gin.Context) {
	taskID, err := uuid.Parse(c.Param("runID"))
	if err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}

	rr := pipeline.ResumeRequest{}
	decoder := json.NewDecoder(c.Request.Body)
	err = errors.Wrap(decoder.Decode(&rr), "failed to unmarshal JSON body")
	if err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}
	result, err := rr.ToResult()
	if err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}

	if err := prc.App.ResumeJobV2(c.Request.Context(), taskID, result); err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	prc.App.GetAuditLogger().Audit(audit.UnauthedRunResumed, map[string]any{"runID": c.Param("runID")})
	c.Status(http.StatusOK)
```

**File:** core/services/chainlink/application.go (L620-627)
```go
	for _, chain := range legacyEVMChains.Slice() {
		legacyChain, ok := chain.(legacyevm.Chain)
		if !ok {
			continue
		}
		legacyChain.HeadBroadcaster().Subscribe(headReporter)
		legacyChain.TxManager().RegisterResumeCallback(pipelineRunner.ResumeRun)
	}
```

**File:** core/services/chainlink/application.go (L1191-1197)
```go
func (app *ChainlinkApplication) ResumeJobV2(
	ctx context.Context,
	taskID uuid.UUID,
	result pipeline.Result,
) error {
	return app.pipelineRunner.ResumeRun(ctx, taskID, result.Value, result.Error)
}
```

**File:** core/services/pipeline/runner.go (L734-757)
```go
func (r *runner) ResumeRun(ctx context.Context, taskID uuid.UUID, value any, err error) error {
	run, start, err := r.orm.UpdateTaskRunResult(ctx, taskID, Result{
		Value: value,
		Error: err,
	})
	if err != nil {
		return fmt.Errorf("failed to update task run result: %w", err)
	}

	// TODO: Should probably replace this with a listener to update events
	// which allows to pass in a transactionalised database to this function
	if start {
		// start the runner again
		go func() {
			ctx, cancel := r.chStop.NewCtx()
			defer cancel()
			if _, err := r.Run(ctx, &run, false, nil); err != nil {
				r.lggr.Errorw("Resume run failure", "err", err)
			}
			r.lggr.Debug("Resume run success")
		}()
	}
	return nil
}
```

**File:** core/bridges/external_initiator.go (L59-67)
```go
// AuthenticateExternalInitiator compares an auth against an initiator and
// returns true if the password hashes match
func AuthenticateExternalInitiator(eia *auth.Token, ea *ExternalInitiator) (bool, error) {
	hashedSecret, err := auth.HashedSecret(eia, ea.Salt)
	if err != nil {
		return false, err
	}
	return subtle.ConstantTimeCompare([]byte(hashedSecret), []byte(ea.HashedSecret)) == 1, nil
}
```

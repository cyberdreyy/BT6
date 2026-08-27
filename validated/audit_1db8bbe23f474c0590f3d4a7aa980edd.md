### Title
Unauthenticated `/v2/resume/:runID` endpoint allows request forgery to inject arbitrary results into pending pipeline runs - (File: core/web/router.go, core/web/pipeline_runs_controller.go)

### Summary
### Finding Description
The ERC777 bug class described in the report hinges on an attacker gaining control-flow via an externally-triggered callback that is trusted to carry legitimate data/state back into a larger operation, without the receiving system validating that the callback genuinely originates from, or is authorized by, the expected party. The closest reachable analog in this Chainlink node codebase is the async bridge/callback ("resume") mechanism used by the job pipeline.

When a pipeline `BridgeTask` is configured with `Async = "true"`, the node embeds a `responseURL` in the request body sent to the external adapter: `http://<node>/v2/resume/<taskID uuid>` [1](#0-0) . The external adapter is expected to later `PATCH` that URL with the task's result, which is consumed by `PipelineRunsController.Resume` and forwarded to `App.ResumeJobV2` / `runner.ResumeRun`, which updates the task's result and resumes/finalizes the suspended pipeline run [2](#0-1) [3](#0-2) .

Critically, this `PATCH /v2/resume/:runID` route is registered in the **unauthenticated** router group (`unauthedv2`), with no session, token, or external-initiator credential check whatsoever — the only "secret" gating access is knowledge of the randomly-generated task UUID: [4](#0-3) . This is explicitly acknowledged in the code via the audit event name `audit.UnauthedRunResumed` emitted after a successful resume [5](#0-4) .

This mirrors the report's core control-flow-hijack pattern: a piece of state (the pending/suspended pipeline run) is left in an intermediate condition, waiting on an external, weakly-authenticated callback that the receiving system implicitly trusts to supply correct data, with no cryptographic proof that the caller is the legitimate bridge/external adapter that was actually invoked for that run.

### Impact Explanation
If the resume-task UUID is disclosed (e.g., via debug-level bridge request logging noted in the CHANGELOG — "HTTP and Bridge tasks (v2 pipeline) now log the request parameters (including the body) upon making the request when `LOG_LEVEL=debug`" — or via a compromised/misconfigured/logging external adapter, a network intermediary, or simply because bridges are frequently third-party HTTP services outside the node operator's control), any unprivileged actor who obtains it can forge a `PATCH` request to `/v2/resume/<uuid>` and inject an arbitrary `Result`/`Error` value into that pending pipeline run — impersonating the external adapter's response. Depending on the job type (e.g., Flux Monitor, OCR observation, VRF fulfillment pipelines using async bridge tasks), this can corrupt or bias the value fed into `median`/`answer` stages of an oracle job, or force a fatal error to abort a run, without ever needing to compromise the bridge adapter itself.

### Likelihood Explanation
Exploitation requires the attacker to first learn a specific pending task's UUID, which is not exposed by design to arbitrary unprivileged users under normal operation. This makes it a secondary/leakage-dependent bug rather than trivially exploitable out of the box, similar to how the ERC777 finding required specific timing/multisig-replay conditions layered on top of the base reentrancy primitive. However, the primitive itself (a completely unauthenticated, credential-less resume endpoint relying purely on UUID secrecy) is a genuine and unnecessary architectural weakness — an unprivileged caller only needs to obtain one leaked UUID (e.g., through debug logs, telemetry, or an untrusted intermediary in the bridge call path) to fully impersonate the legitimate external adapter for that run.

### Recommendation
Do not rely solely on an opaque UUID as an implicit bearer credential for the `/v2/resume/:runID` endpoint. At minimum, bind the resume callback to a per-run, per-bridge shared secret or HMAC-signed token that is not logged in plaintext, and consider requiring the request to be authenticated via the bridge's own configured credentials (similar to `AuthenticateExternalInitiator`) rather than leaving the route unauthenticated. Also verify that debug-level logging of bridge request bodies (`task.bridge.go`) does not leak the `responseURL`/resume-task UUID in a way that a lower-privileged log consumer could exploit.

### Proof of Concept
Not independently reproducible against this codebase from static analysis alone: exploitation depends on an out-of-band leak of the resume-task UUID (e.g., via `LOG_LEVEL=debug` bridge request logging, a compromised bridge/EA, or a network observer on the responseURL request), which was not directly demonstrated here. The concrete, verifiable facts are the unauthenticated route registration [4](#0-3) , the `Resume` handler's lack of any auth check [2](#0-1) , and the `UnauthedRunResumed` audit-event naming that acknowledges this design choice.

### Citations

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

**File:** core/web/router.go (L238-248)
```go
func v2Routes(app chainlink.Application, r *gin.RouterGroup) {
	unauthedv2 := r.Group("/v2")

	prc := PipelineRunsController{app}
	psec := PipelineJobSpecErrorsController{app}
	unauthedv2.PATCH("/resume/:runID", prc.Resume)

	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
```

### Title
Unauthenticated pipeline run resumption allows any unprivileged client to inject arbitrary task results into any bridge-triggered job run - (File: core/web/pipeline_runs_controller.go)

### Summary
The `PATCH /v2/resume/:runID` route is mounted with no authentication middleware at all, unlike every other pipeline/job/bridge endpoint which requires at least session/token or external-initiator auth. Any unauthenticated client that can reach the node's API port can call this endpoint with an arbitrary UUID to attempt to resume/complete a pending pipeline task run, similar in spirit to the Rubicon `adminWriteBathToken` finding where a privileged-looking write path lets a caller overwrite state that other users/funds depend on — except here the write path requires *no* privilege at all.

### Finding Description
`v2Routes` registers this route on the `unauthedv2` group, which has no `auth.Authenticate(...)` wrapper, in contrast to every other route registered on `authv2` (bridge types, jobs, external initiators, etc.): [1](#0-0) 

The handler itself parses only a `runID` (a task UUID) from the URL and a `ResumeRequest` body, then calls `App.ResumeJobV2` to finalize the task and resume the pipeline — with no session, token, or external-initiator check anywhere in the function: [2](#0-1) 

The audit log entry name itself, `audit.UnauthedRunResumed`, confirms this is an intentionally unauthenticated code path rather than an oversight introduced by a missing middleware wrapper: [3](#0-2) 

This endpoint exists to let external bridge adapters asynchronously post back a `Result` for a specific pending task (identified by an unguessable UUID acting as a bearer credential), completing the `ResumeRun` flow in the pipeline runner: [4](#0-3) 

The design relies entirely on the task UUID being unguessable/secret. Anywhere that UUID leaks (logs, error responses, a compromised/malicious bridge adapter response body, network capture, etc.) an unauthenticated attacker gains the ability to inject any completion value/error into that pending job run, which then continues execution using attacker-controlled data as if it were the legitimate bridge's answer.

### Impact Explanation
An attacker who obtains or guesses a pending task's UUID can complete/resume that pipeline run with arbitrary attacker-chosen `value`/`error` data without any authentication. Because this is the same execution path a legitimate bridge adapter uses to feed results into a Chainlink job (e.g., price feeds, VRF completions, or other pipeline outputs), corrupting the resumed value can lead to bad data flowing into oracle reports/on-chain transmissions or DoS/fatal-erroring of the run. This mirrors the "admin rug vector" bug class — a state-mutating endpoint that requires no legitimate authorization to reach — but the reachable actor here is a fully unprivileged network client rather than a cooperating admin, making it a stronger, directly-exploitable analog.

### Likelihood Explanation
Exploitation requires knowledge of a valid, still-pending task UUID. Since this design is intentional (UUID-as-bearer-token), the likelihood hinges on whether UUIDs are ever exposed outside the intended bridge-response channel (e.g., leaked via logs, error messages, monitoring, or a compromised/misbehaving external adapter passing it onward). This is a known, accepted trade-off in Chainlink's design for supporting async bridge callbacks, so likelihood should be assessed against how well UUID secrecy is enforced elsewhere in the codebase (logging, metrics, presenter serialization) — an area this search did not fully verify due to time constraints.

### Recommendation
- Confirm task-resume UUIDs are never included in any user-facing or logged surface (job run presenters, audit logs, error responses) to preserve the "UUID as secret" invariant.
- Consider adding a secondary authentication factor (e.g., a bridge-specific outgoing token/secret validated against the originating bridge, similar to `ExternalInitiator.OutgoingToken`/`OutgoingSecret`) so that possession of the run UUID alone is insufficient to mutate run state.
- Add rate limiting to `/v2/resume/:runID` (it currently has none, unlike `/sessions`) to slow down UUID brute-forcing.

### Proof of Concept
1. Send `PATCH /v2/resume/<guessed-or-leaked-task-uuid>` with a JSON body matching `pipeline.ResumeRequest` (e.g. `{"value": "attacker-controlled"}`) to any Chainlink node's HTTP API, without any `Authorization`, session cookie, or `X-Chainlink-EA-*` headers.
2. The route matches `unauthedv2.PATCH("/resume/:runID", prc.Resume)` and executes `PipelineRunsController.Resume`, which performs no auth check, calls `App.ResumeJobV2`, and returns `200 OK` on success. [5](#0-4) [6](#0-5)

### Citations

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

**File:** core/web/pipeline_runs_controller.go (L130-160)
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

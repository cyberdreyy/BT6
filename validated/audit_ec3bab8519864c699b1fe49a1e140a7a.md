Based on the investigation, the strongest reachable analog in this Chainlink node codebase is the unauthenticated pipeline-run resume endpoint, which parallels the report's core defect: a callback/resumption entry point that accepts attacker-controlled data with no verification that the caller is the legitimate, expected initiator of that specific pending operation.

### Title
Unauthenticated job-run resumption allows arbitrary task result injection - (File: core/web/pipeline_runs_controller.go)

### Summary
The `/v2/resume/:runID` route is registered with no authentication middleware at all, unlike virtually every other v2 API route which requires session, API-token, or external-initiator authentication. [1](#0-0) 

### Finding Description
`PipelineRunsController.Resume` accepts a `runID` (a task UUID) and an arbitrary JSON body (`pipeline.ResumeRequest`), then calls `App.ResumeJobV2`, which is forwarded directly into the pipeline `Runner.ResumeRun` to set the task-run's result value/error and restart the suspended pipeline. [2](#0-1) [3](#0-2) [4](#0-3) 

This mirrors the reported bug class precisely: like GMX's `afterDepositExecution`, which accepts whatever `Deposit.Props` is delivered without checking that the `account` field corresponds to the legitimate initiating contract, `Resume` accepts whatever result payload arrives for a given `runID` without any cryptographic or session-based proof that the caller is the external adapter/bridge that the pipeline is actually waiting on. The only "authorization" is possession of the `runID` UUID itself — there is no shared per-run secret, HMAC, signature, or IP/allowlist check tying the resumer to the originating bridge task, and the route explicitly bypasses the `auth.Authenticate` middleware used everywhere else in the router. [1](#0-0) 

The audit log entry itself is even named `UnauthedRunResumed`, indicating this is a recognized unauthenticated code path. [5](#0-4) 

### Impact Explanation
If a `runID` is disclosed (e.g., via logs, timing/side channels, or a leaky bridge adapter response), any unprivileged network client can PATCH arbitrary result data into a suspended pipeline task and force the pipeline to resume with attacker-chosen values. Depending on the job graph, this can feed forged data into downstream tasks (e.g., `ethtx`, on-chain report submission), potentially triggering unauthorized job completion/fund-moving transactions — directly analogous to the "unrestricted position fulfillment" bug where an attacker-supplied callback payload drove a privileged operation (`fulfillOpenPosition`) that should have required verifying the initiator.

### Likelihood Explanation
Exploitation requires the attacker to obtain a valid, still-pending `runID`. This is a real constraint (UUIDs are not brute-forceable), but the route's complete lack of an authentication layer means the security model rests entirely on ID secrecy rather than any authenticated relationship between the pipeline run and the resumer — the same architectural gap the external report calls out (trusting an unauthenticated actor's data without validating it originated from the expected initiator).

### Recommendation
Require that resume requests are authenticated as originating from the specific external initiator/bridge that the pending task expects (e.g., a per-run secret/token bound at task-creation time, or requiring the external-initiator credential used to originally trigger the job), rather than relying solely on knowledge of the `runID`.

### Proof of Concept
Not independently verifiable from static code alone since exploitation depends on `runID` disclosure through operational channels (logs, adapter responses, race conditions) rather than a pure code-level bypass; this is noted as an uncertainty. The route wiring itself, however, concretely confirms the missing authentication layer: [1](#0-0)

### Citations

**File:** core/web/router.go (L238-244)
```go
func v2Routes(app chainlink.Application, r *gin.RouterGroup) {
	unauthedv2 := r.Group("/v2")

	prc := PipelineRunsController{app}
	psec := PipelineJobSpecErrorsController{app}
	unauthedv2.PATCH("/resume/:runID", prc.Resume)

```

**File:** core/web/pipeline_runs_controller.go (L133-159)
```go
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

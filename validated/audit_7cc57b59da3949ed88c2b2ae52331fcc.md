This endpoint's lack of an auth wrapper is intentional design, not a bypass. The security model for async bridge resumption relies on the task UUID itself functioning as an unguessable bearer credential.

### Title
No vulnerability - unauthenticated PATCH /v2/resume/:runID is a designed bearer-token callback, not an auth bypass - (File: core/web/router.go, core/web/pipeline_runs_controller.go)

### Summary
The route `unauthedv2.PATCH("/resume/:runID", prc.Resume)` is deliberately left outside the `auth.Authenticate` group because it is the callback endpoint that external bridge adapters use to asynchronously deliver results for a pending pipeline task. The security boundary here is not a session/token, but the task's UUID (`runID`), which is generated server-side and embedded only in the `responseURL` sent privately to the bridge adapter.

### Finding Description
`v2Routes` in [1](#0-0) registers `PATCH /v2/resume/:runID` on the `unauthedv2` group before any `auth.Authenticate` middleware is applied, while every other pipeline-runs mutation (e.g. `POST /v2/jobs/:ID/runs`) requires `auth.AuthenticateExternalInitiator`/token/session. `PipelineRunsController.Resume` parses `runID` as a UUID and calls `prc.App.ResumeJobV2` [2](#0-1) , which flows to `runner.ResumeRun`, calling `orm.UpdateTaskRunResult(ctx, taskID, ...)` [3](#0-2) . The ORM query explicitly scopes the update to `pipeline_task_runs.id = $1 AND pipeline_runs.state in ('running','suspended')` [4](#0-3) , meaning only a task run matching that exact UUID and currently suspended can be affected — there is no cross-run/cross-task ambiguity to exploit beyond guessing the UUID itself.

The UUID (`t.uuid`) that must be guessed is only ever embedded in the `responseURL` field sent to the external bridge adapter over the bridge's configured URL, constructed in `BridgeTask.finalizeAndMarshalBridgeRequestData` as `/v2/resume/<uuid>` [5](#0-4) . This is a standard "unguessable capability URL" pattern (128-bit random UUIDv4), functionally equivalent to a bearer token issued out-of-band to the trusted bridge adapter. This is the same rationale documented by the existing negative test in `TestRBAC_Routemap_Admin`, which explicitly asserts `PATCH /v2/resume/1` should NOT return `401`/`403` [6](#0-5) , and by the audit event name itself: `audit.UnauthedRunResumed` [7](#0-6) , which confirms this route's unauthenticated nature is intentional and explicitly audited/logged.

To exploit this as claimed, an attacker would need to correctly guess a valid, currently-suspended pipeline task run's UUID — a random 122-bit-entropy value never exposed via any other unauthenticated endpoint — which is not a credential/auth bypass but a brute-force problem against an intentionally-designed capability token, infeasible in practice.

### Impact Explanation
No meaningfully exploitable privilege escalation exists under the stated attacker model (no credential leak assumed). Guessing a valid UUIDv4 tied to an in-flight suspended pipeline run is computationally infeasible, and the design already restricts effect to only the exact suspended task matching that ID.

### Likelihood Explanation
Not exploitable without already possessing/leaking the task UUID (e.g., via log exposure, MITM on bridge callback, or another separate vulnerability), which is outside this question's scope (auth soundness of the route itself).

### Recommendation
No action required for this specific finding. If desired defense-in-depth is wanted, consider rate-limiting `/v2/resume/:runID` more strictly (it already sits under `rl.Authenticated()`/general rate limiter per `NewRouter`) or rotating/short-lived signed callback tokens instead of raw UUIDs, but this is hardening, not a vulnerability fix.

### Proof of Concept
N/A — no valid PoC demonstrates unauthorized access to another user's run without already knowing/leaking that run's UUID, which is out of scope per the attacker model (no credential/secret access assumed).

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

**File:** core/services/pipeline/orm.go (L276-286)
```go
		sql := `
		SELECT pipeline_runs.*, pipeline_specs.dot_dag_source "pipeline_spec.dot_dag_source", job_pipeline_specs.job_id "job_id"
		FROM pipeline_runs
		JOIN pipeline_task_runs ON (pipeline_task_runs.pipeline_run_id = pipeline_runs.id)
		JOIN pipeline_specs ON (pipeline_specs.id = pipeline_runs.pipeline_spec_id)
		JOIN job_pipeline_specs ON (job_pipeline_specs.pipeline_spec_id = pipeline_specs.id)
		WHERE pipeline_task_runs.id = $1 AND pipeline_runs.state in ('running', 'suspended')
		FOR UPDATE`
		if err = tx.ds.GetContext(ctx, &run, sql, taskID); err != nil {
			return fmt.Errorf("failed to find pipeline run for task ID %s: %w", taskID.String(), err)
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

**File:** core/web/auth/auth_test.go (L337-337)
```go
	{"DELETE", "/v2/nodes/evm/forwarders/MOCK", false, false, true},
```

**File:** core/logger/audit/audit_types.go (L1-1)
```go
package audit
```

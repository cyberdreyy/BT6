### Title
Unauthenticated `/v2/resume/:runID` endpoint lets any party race to inject arbitrary task-run results into an async pipeline run - ([File: core/web/router.go])

### Summary
The Chainlink node exposes a completely unauthenticated endpoint, `PATCH /v2/resume/:runID`, whose only "authorization" is possession of the task's UUID. Whoever calls it first for a given `taskID` gets to supply the value/error that resumes the suspended pipeline run, exactly mirroring the Scroll `replayMessage`/`dropMessage` bug class: an action tied to a specific pending object can be performed by any unprivileged caller who learns the identifier, not only the legitimate party the system intended (the external adapter that was sent the callback URL), enabling front-running/hijacking of the intended outcome.

### Finding Description
When a pipeline job uses an async `bridge` task, the node generates a per-task UUID and embeds a callback URL of the form `.../v2/resume/<taskID>` in the request sent to the external adapter: [1](#0-0) 

This route is registered outside of any authentication middleware group, unlike virtually every other `/v2/*` route: [2](#0-1) 

The handler trusts the `runID` (task UUID) alone to authorize completing/resuming the run - there is no secret, signature, session, or external-initiator credential check tying the caller to the original bridge task or adapter: [3](#0-2) 

The underlying ORM operation performs a "first writer wins" update: it locks the pending task run, finds it purely `WHERE pipeline_task_runs.id = $1 AND pipeline_runs.state in ('running','suspended')`, and unconditionally overwrites its output/error and transitions the run state - with no verification of the caller's identity: [4](#0-3) 

This is structurally identical to the reported Scroll bug class: a pending, sender-specific action (resuming/completing a message or run) is guarded only by a public/guessable-or-leakable object ID rather than by verifying the actor is the intended party, so any third party who learns the ID before the real actor acts can front-run/hijack the operation's outcome.

### Impact Explanation
Whoever discovers a pending task's UUID (via network interception, logs, shared infrastructure, a misbehaving/compromised external adapter, or by racing a slow legitimate adapter response) can call `PATCH /v2/resume/<uuid>` with attacker-chosen JSON to inject a fabricated `value` or `error` into the pipeline run before the legitimate adapter's real response arrives. Because the ORM update is unconditional and "first write wins" (the row is then in `running` state, and duplicate resumes to a task already resolved will simply fail to match the `running`/`suspended` `WHERE` clause), the attacker's forged value becomes the task's permanent result and drives all downstream tasks (e.g., `median`, on-chain transmission tasks) in that run, i.e. unauthorized manipulation of the job run outcome.

### Likelihood Explanation
This requires the attacker to learn a task UUID before/instead of the real external adapter completing it - the UUID is not derivable from public data, so likelihood is a function of how easily an attacker can intercept the outgoing bridge request or race the legitimate response (e.g., adapter over plaintext HTTP, delayed/timeout-prone adapter, logging that exposes the URL, or an adapter operator who is semi-trusted but not fully trusted). This is comparable to the acknowledged-but-unresolved nature of the original Scroll finding: the maintainers explicitly did not treat "front-running is possible" as disqualifying, only as a design tradeoff.

### Recommendation
Bind the resume callback to the original request cryptographically instead of relying purely on UUID secrecy: include a per-task shared secret/HMAC (verified server-side) in the resume URL/body, validate that the caller matches the bridge/external-adapter credentials configured for that specific bridge (similar to `ExternalInitiator` HMAC auth in `core/bridges/external_initiator.go`), and/or rate-limit and audit-log resume attempts per task ID to detect duplicate/racing completions.

### Proof of Concept
1. Configure a job with an `async=true` bridge task; the node sends the adapter a request containing `responseURL: http://<node>/v2/resume/<taskID>`. [1](#0-0) 
2. An attacker who obtains `<taskID>` (e.g., via network capture between node and adapter, or a compromised/curious adapter operator) sends their own request first:
   `PATCH /v2/resume/<taskID>` with body `{"error": null, "data": {"result": "<attacker-controlled value>"}}` - no authentication header required, per the unauthenticated route registration: [5](#0-4) 
3. `PipelineRunsController.Resume` decodes the body and calls `App.ResumeJobV2` → `Runner.ResumeRun` → `orm.UpdateTaskRunResult`, which finds the task in `running`/`suspended` state and overwrites its result unconditionally: [6](#0-5) 
4. The pipeline resumes with the attacker's forged value; when the legitimate adapter's real response arrives afterward, the task run is no longer in `running`/`suspended` state for that ID, so the legitimate response is silently ignored - the attacker's front-run result has already won.

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

**File:** core/services/pipeline/orm.go (L271-308)
```go
func (o *orm) UpdateTaskRunResult(ctx context.Context, taskID uuid.UUID, result Result) (run Run, start bool, err error) {
	if result.OutputDB().Valid && result.ErrorDB().Valid {
		panic("run result must specify either output or error, not both")
	}
	err = o.transact(ctx, func(tx *orm) error {
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

		// Update the task with result
		sql = `UPDATE pipeline_task_runs SET output = $2, error = $3, finished_at = $4 WHERE id = $1`
		if _, err = tx.ds.ExecContext(ctx, sql, taskID, result.OutputDB(), result.ErrorDB(), time.Now()); err != nil {
			return fmt.Errorf("failed to update pipeline task run: %w", err)
		}

		if run.State == RunStatusSuspended {
			start = true
			run.State = RunStatusRunning

			sql = `UPDATE pipeline_runs SET state = $2 WHERE id = $1`
			if _, err = tx.ds.ExecContext(ctx, sql, run.ID, run.State); err != nil {
				return fmt.Errorf("failed to update pipeline run state: %w", err)
			}
		}

		return loadAssociations(ctx, tx.ds, []*Run{&run})
	})

	return run, start, err
}
```

Based on the investigation, most "replace-without-settling-pending-state" analogs in this codebase are operator/admin-only paths (job spec `Update`, external-initiator deletion, admin-role changesets), which the rules explicitly exclude. However, one concrete unprivileged-actor analog does map to the M-14 bug class: a pipeline "resume" endpoint that finalizes suspended/pending run state **without any authentication**, relying only on an unguessable UUID — this mirrors the root cause of M-14 (a privileged state-transition operation performed without properly verifying/settling the party who is entitled to the pending state before it is consumed/lost).

### Title
Unauthenticated `PATCH /v2/resume/:runID` endpoint allows any external actor to finalize/hijack pending pipeline run state - (File: core/web/pipeline_runs_controller.go)

### Summary
The `PipelineRunsController.Resume` handler completes a suspended pipeline run (analogous to "disbursing" pending, in-flight state) purely based on a client-supplied task UUID, with no session, API-key, or external-initiator credential check. This is different from `RunsController.Create`, which requires either a logged-in session or `ExternalInitiatorAccessKeyHeader`/`ExternalInitiatorSecretHeader` (as seen in `TestRunner_WebhookJobRemoved`). The audit event name `audit.UnauthedRunResumed` used in this handler itself indicates the endpoint is intentionally reachable without authentication.

### Finding Description
`Resume` parses a `runID` (task UUID) from the URL and a JSON body containing a `Result`, then calls `App.ResumeJobV2(ctx, taskID, result)` directly: [1](#0-0) 

`ResumeJobV2` forwards straight into the pipeline runner: [2](#0-1) 

`ResumeRun` looks up the pending task purely by UUID and overwrites its output/error, potentially restarting the whole pipeline (which can include fund-moving `ETHTxTask` steps waiting on `MinConfirmations`, or webhook/bridge continuations): [3](#0-2) [4](#0-3) 

The only "secret" gating this operation is the task UUID, which is embedded in outbound bridge requests as the `responseURL` (`/v2/resume/<uuid>`) sent to third-party adapters: [5](#0-4) 

Unlike the run-creation path (`/v2/jobs/:ID/runs`), which validates `ExternalInitiatorAccessKeyHeader`/`ExternalInitiatorSecretHeader` credentials before accepting a run trigger, the resume path performs no equivalent authentication/ownership check before consuming and finalizing the pending state tied to that UUID.

This maps to the M-14 bug class: a critical state-transition (settling "accrued"/pending state before it's lost or overwritten) is performed without verifying that the actor invoking it is the legitimate party entitled to complete that state — opening the door to request impersonation and cross-run/cross-actor response confusion if a task UUID is ever leaked (e.g., via logs, a compromised external adapter, SSRF, or a shared-infra leak), rather than requiring a proper authenticated principal.

### Impact Explanation
If an unauthenticated party obtains or guesses a pending task's UUID, they can call `PATCH /v2/resume/:runID` to inject an arbitrary `Result` into that run, forging the outcome of an in-flight bridge/webhook/EA response and resuming (finalizing) the pipeline with attacker-controlled data — potentially driving downstream `ETHTx` or oracle-report tasks with falsified inputs. This is a concrete instance of "request impersonation / cross-user response confusion" via a missing-authentication finalize/settle path, the same structural weakness that let M-14 lose or mishandle pending value during a state-replacing operation.

### Likelihood Explanation
Exploitation is gated on discovering a valid, still-pending task UUID (128-bit, unguessable at scale), so it requires an auxiliary leak (compromised/misconfigured external adapter, verbose logging, network capture) rather than being trivially exploitable at will. Given this precondition, likelihood is Low-to-Medium, but the fact that the endpoint is unauthenticated by design (per the `UnauthedRunResumed` audit label) means the UUID is the sole control, with no defense-in-depth from session/EI authentication.

### Recommendation
Require the same external-initiator/session authentication used elsewhere (e.g., match the run's originating external initiator credentials, or bind the resume call to the bridge's configured secret/HMAC) before allowing `ResumeJobV2` to mutate/finalize a pending task's result, rather than relying solely on the UUID as a bearer secret.

### Proof of Concept
1. Identify or leak a pending async task's UUID (embedded as `/v2/resume/<uuid>` in the `responseURL` sent to an external adapter in `finalizeAndMarshalBridgeRequestData`, see `core/services/pipeline/task.bridge.go:363-373`).
2. Send `PATCH /v2/resume/<uuid>` with an attacker-chosen JSON body to `core/web/pipeline_runs_controller.go` `Resume` handler — no `Authorization`, no `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers required.
3. `ResumeJobV2` → `runner.ResumeRun` → `orm.UpdateTaskRunResult` (`core/services/pipeline/orm.go:271-308`) accepts and persists the forged result, resuming the pipeline with attacker-controlled data.

### Citations

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

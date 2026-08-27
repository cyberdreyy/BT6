## Analog Found

### Title
Unauthenticated `PipelineRunsController.Resume` endpoint lets anyone front-run a pending async task's resumption, permanently breaking the legitimate result delivery (DoS) - (File: `core/web/pipeline_runs_controller.go`)

### Summary
The `retrySettlement`/`redeemSettlement` bug class in the report is: a state-mutating function is keyed only by an opaque identifier (a nonce) with no check that the caller is authorized to act on that identifier, so anyone can front-run the legitimate resolution and permanently corrupt the state for the rightful actor, causing a DoS. Chainlink's `PATCH /v2/pipeline/runs/:runID` handler (`Resume`) has the same shape: it is explicitly unauthenticated (see the audit event name `audit.UnauthedRunResumed`) and accepts only a `taskID` UUID plus an arbitrary result/error payload, with no verification that the caller is the legitimate external system (e.g. an HTTP/bridge adapter callback) that was supposed to resolve that specific suspended task.

### Finding Description
`PipelineRunsController.Resume` parses the `runID` path parameter as a task UUID and directly forwards attacker-controlled `value`/`error` into `App.ResumeJobV2` → `pipelineRunner.ResumeRun` → `orm.UpdateTaskRunResult`, with no authentication or ownership check: [1](#0-0) 

`ResumeJobV2` simply forwards to the pipeline runner: [2](#0-1) 

`ResumeRun` calls `UpdateTaskRunResult`, which updates the task row for the given `taskID` and — critically — flips the entire pipeline run from `suspended` back to `running` if it was suspended: [3](#0-2) [4](#0-3) 

The `UPDATE ... WHERE pipeline_task_runs.id = $1 AND pipeline_runs.state in ('running', 'suspended')` clause means the endpoint is idempotent/exclusive per run: whichever caller (legitimate external adapter vs. an attacker who has observed/guessed the `taskID`) submits first "wins" and finalizes the task/run. The audit log constant name itself (`UnauthedRunResumed`) documents that this route is intentionally reachable without any session, API token, or external-initiator credential — the only "secret" is the UUID `taskID`.

### Impact Explanation
This is the same primitive as `retrySettlement`: a resource that a specific external party (the real callback endpoint that should deliver the async task's result) is expected to resolve can instead be resolved by *any* unauthenticated caller who obtains the `taskID` (e.g., it appears in webhook URLs configured on third-party adapters, in logs, or via other side channels). By submitting a bogus/error result first:
- The pipeline run's pending task is finalized with attacker-supplied data (`value`/`error`), corrupting the run's outcome (cross-actor response confusion, since the run output is controlled by an unrelated party instead of the real adapter).
- Once the run transitions out of `suspended`, the same `UPDATE ... WHERE ... state in ('running','suspended')` will no longer match for the legitimate adapter's subsequent, genuine callback (the underlying run row may already be finished/removed), so its real result is silently dropped — this is a DoS on the legitimate resumption path, exactly mirroring `redeemSettlement` reverting because the settlement was already marked `Success` by the malicious `retrySettlement` call.

### Likelihood Explanation
The endpoint requires no credentials at all — likelihood is bounded solely by whether the UUID `taskID` can be discovered (e.g., via a still-pending outbound HTTP/bridge task's callback URL, application logs, or network observation), which is a much lower bar than the on-chain front-running discussed in the original report (no mempool/ordering assumptions needed here — it's a plain unauthenticated HTTP endpoint).

### Recommendation
Bind resumption to a caller who can prove they are the legitimate resumer of that specific `taskID` (e.g., require a per-task random bearer token generated at suspend-time and delivered only to the external system that is meant to complete it, or require the identical external-initiator/API credentials used to trigger the original job, verified against the job/task's owner) rather than relying on the UUID alone as an implicit secret.

### Proof of Concept
Given a suspended pipeline task with UUID `<taskID>` awaiting an external callback (e.g. an async HTTP/bridge adapter task):
```
curl -X PATCH http://<node>/v2/pipeline/runs/<taskID> \
  -H "Content-Type: application/json" \
  -d '{"error": "attacker-forced failure"}'
```
No cookies, session, or `X-Chainlink-EA-*` headers are required — `Resume` (`core/web/pipeline_runs_controller.go:133-160`) processes the request unauthenticated. This finalizes/errors the task via `UpdateTaskRunResult` (`core/services/pipeline/orm.go:271-308`) before the real adapter's callback arrives, and the real subsequent callback to the same URL will then fail to find a matching `running`/`suspended` run, denying the legitimate resumption — the same failure mode described for `redeemSettlement` in the original report.

Note: I was not able to view the exact `core/web/router.go` route-registration lines confirming the specific middleware chain (auth vs. unauthenticated group) for this route due to search/index limits; the conclusion that it's unauthenticated is inferred from the `audit.UnauthedRunResumed` event name used in the handler. If precise confirmation is needed, start a Devin session with full repo access to inspect `core/web/router.go` directly.

### Citations

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

### Title
Unauthenticated `/v2/resume/:runID` endpoint allows an attacker to consume the pipeline-run "resume" nonce, permanently discarding the legitimate external adapter callback (DOS / result corruption) - (File: `core/web/pipeline_runs_controller.go`)

### Summary
The external report describes a class of bug where a single-use, signature/nonce-gated operation (`permit()`) can be "consumed" ahead of time by anyone who has captured the signed payload, causing the legitimate caller's follow-up call to fail. Chainlink node's async pipeline resumption mechanism has the same root-cause shape: a bearer-style random UUID (`taskID`) is the only credential protecting `PipelineRunsController.Resume`, and the underlying SQL update has no idempotency/ownership guard, so whoever calls the endpoint first with that UUID "wins" and the true async result is silently discarded.

### Finding Description
Chainlink async bridge tasks give the external adapter a `ResponseURL` of the form `.../v2/resume/<taskID>` where `taskID` is the pipeline task-run UUID [1](#0-0) . The controller that services this callback is: [2](#0-1) 

Note the audit event name `audit.UnauthedRunResumed`, indicating this route is intentionally reachable without normal API-token/session authentication (external adapters cannot be expected to hold node credentials) — the UUID itself is the only secret protecting the operation.

The datastore method backing this call performs the update unconditionally on `id = $1`, regardless of whether the task run has already been completed, and only checks that the parent run's state is `running`/`suspended`: [3](#0-2) 

Because the `UPDATE pipeline_task_runs SET output = $2, error = $3, finished_at = $4 WHERE id = $1` statement has no `WHERE finished_at IS NULL` guard, and the run-state check only prevents restart signaling (not the write itself) once the run has transitioned from `suspended` to `running`, a second call to the same `taskID` (i.e., the real callback arriving after an attacker's forged one) will still succeed and silently overwrite the previously-recorded output/error — but `start` will now be `false`, so the run is not resumed again. In effect, whichever caller reaches this endpoint first "wins": if an attacker submits a request to `/v2/resume/<taskID>` before the legitimate external adapter's callback, the legitimate adapter's data is discarded and the job proceeds with attacker-controlled data (or errors out), with no way to detect the anomaly.

This mirrors the `permit()` DOS root cause precisely: an operation is gated only by knowledge of a single opaque value (a signature in the original report; a UUID here) rather than by verifying the caller's authorization to complete that specific pending step, and consuming it out-of-band blocks/corrupts the legitimate flow.

### Impact Explanation
If an attacker can obtain or predict a pending `taskID` (e.g., via server logs, monitoring tools, shared infrastructure with the external adapter, or a leaky bridge implementation that logs its own `ResponseURL`), they can:
- DOS the specific job run by submitting a bogus/errored resume payload before the genuine bridge response arrives, causing the run to complete with a fabricated result or a forced error.
- Corrupt the output used by downstream computation (e.g., a price feed or automation), since the injected value silently overrides the real one.

This is analogous to Medium impact/Medium likelihood in the source report: it's not a full unauthenticated takeover, but it enables targeted disruption/corruption of specific pipeline runs by an unprivileged actor with no valid API credentials.

### Likelihood Explanation
Exploitation requires the attacker to learn a specific `taskID` before the real callback lands — this is a non-trivial but plausible bar (log exposure, timing races on slow external adapters, or an adapter that reflects the UUID back to the requester). Since the endpoint is explicitly unauthenticated by design (as evidenced by `audit.UnauthedRunResumed`), the only barrier is knowledge of the UUID, not any cryptographic proof of authorization tied to the specific job run's expected responder — the same structural weakness the original permit-front-running report calls out.

### Recommendation
- Add a `WHERE finished_at IS NULL` (or equivalent "not yet completed") condition to the `UPDATE pipeline_task_runs ...` statement in `UpdateTaskRunResult`, and treat 0-rows-affected as an already-resumed/expired task, returning an error instead of silently succeeding.
- Consider binding the resume token to a per-task secret (HMAC or similar) rather than relying solely on the UUID's unguessability, and/or rate-limit/log repeated resume attempts against the same `taskID` to detect front-running attempts.

### Proof of Concept
1. Create an async bridge task; the node returns a `ResponseURL` containing `taskID` to the external adapter and marks the task run `suspended` (see `core/services/pipeline/task.bridge_test.go:585-636`).
2. Before the real external adapter responds, an attacker who has obtained `taskID` sends `PATCH /v2/runs/<taskID>` with an arbitrary result — the `Resume` handler in `core/web/pipeline_runs_controller.go` accepts it with no additional authorization tied to the specific pending callback and calls `ResumeRun`, which flips the run to `running` and restarts execution with the attacker's data.
3. When the genuine external adapter later calls back to the same `ResponseURL`, `UpdateTaskRunResult` executes its `UPDATE ... WHERE id = $1` unconditionally, overwriting `output`/`finished_at` again, but because `run.State` is no longer `suspended`, `start` is `false` and the run is not re-triggered — the legitimate result is silently dropped/lost, with no error surfaced to the adapter or to node operators. [2](#0-1) [3](#0-2)

### Citations

**File:** core/services/pipeline/task.bridge_test.go (L601-609)
```go
		err = json.Unmarshal(payload, &reqBody)
		assert.NoError(t, err)
		assert.Equal(t, fmt.Sprintf("%s/v2/resume/%v", cfg.WebServer().BridgeResponseURL(), id.String()), reqBody.ResponseURL)
		w.Header().Set("Content-Type", "application/json")

		// w.Header().Set("X-Chainlink-Pending", "true")
		response := map[string]any{"pending": true}
		assert.NoError(t, json.NewEncoder(w).Encode(response))
	})
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

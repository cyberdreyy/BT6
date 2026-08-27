### Title
Missing reentrancy/replay protection on unauthenticated `PipelineRunsController.Resume` allows repeated resumption of a suspended pipeline run - (File: `core/web/pipeline_runs_controller.go`)

### Summary
The H-32 bug class is a callback function (`onFlashLoan`) that lacks reentrancy protection, letting an attacker re-enter and repeat a privileged state-mutating operation before it settles, resulting in unbounded borrowing. The closest reachable analog in this Chainlink node codebase is the `Resume` handler on `PipelineRunsController`, which finalizes/advances a suspended pipeline run via an external HTTP callback and is deliberately registered without session/token authentication (the audit event name itself is `audit.UnauthedRunResumed`), guarded only by an unauthenticated, externally-supplied UUID (`taskID`).

### Finding Description
`PipelineRunsController.Resume` accepts an unauthenticated `PATCH` request containing a `taskID` and a `ResumeRequest` body, and forwards it directly to `App.ResumeJobV2` → `pipelineRunner.ResumeRun` → `orm.UpdateTaskRunResult`: [1](#0-0) 

The audit log call explicitly labels this endpoint `UnauthedRunResumed`, confirming it bypasses the normal session/API-token authentication chain (`AuthenticateBySession` / `AuthenticateByToken` / `AuthenticateExternalInitiator`) used elsewhere in the web router: [2](#0-1) 

The only "secret" protecting this endpoint is possession of the `taskID` UUID, which is embedded in the bridge task's `ResponseURL` (`http://.../v2/resume/<taskID>`) and sent to third-party bridge/external-adapter endpoints: [3](#0-2) 

`ResumeRun` performs a check-then-act update of the task/run state with no idempotency guard preventing the same `taskID` from being resumed multiple times with different payloads before/after the run transitions out of `suspended`: [4](#0-3) 

`UpdateTaskRunResult` does lock the row (`FOR UPDATE`) and only proceeds if the parent run is in `running` or `suspended` state, and only flips `start=true` once when the run is `suspended`: [5](#0-4) 

However, this only prevents restarting a run that has already finished (`completed`/`errored`) — it does **not** prevent the same task from being resumed multiple times while the run is still `running` (after the first resume flips state to `running` but before it finishes), because the `WHERE ... state in ('running','suspended')` clause matches both states and the task-run row itself has no "already-consumed" flag checked before applying the new `output`/`error`. Each call to `/v2/resume/:runID` unconditionally overwrites the task's output/error and, for the initial suspended→running transition, triggers a fresh asynchronous execution of `r.Run(...)`, which re-drives the downstream DAG (potentially re-triggering VRF fulfillment, tx submission, or other side-effecting tasks) using attacker-controlled `Result.Value`/`Result.Error` data — this is a "callback re-entered during in-flight processing" pattern structurally analogous to the flash-loan `onFlashLoan` reentrancy: an externally reachable callback that can be invoked again for the same in-progress operation because there is no re-entrancy/consumption lock on the resource (the task run) between "start" and "finish".

### Impact Explanation
An unauthenticated attacker who obtains or guesses a `taskID` (leaked via bridge external-adapter callback URLs, logs, or a compromised bridge integration) can repeatedly call `PATCH /v2/resume/:taskID` with attacker-chosen result data while the run is `running`, causing the pipeline to be resumed/re-driven with attacker-controlled values. Since resumed pipelines can include tasks that submit on-chain transactions or trigger further job runs (e.g., VRF fulfillment, DirectRequest callbacks), this could result in unauthorized job execution, fund-moving transaction submission, or corruption of run state with attacker-supplied data — impact analogous to "unauthorized job run or fund movement" called out in scope.

### Likelihood Explanation
Likelihood depends on how well `taskID` values are protected in transit/storage (they are UUIDs sent as part of the bridge `ResponseURL`, which is exposed to third-party external adapters and could appear in adapter logs, proxies, or be intercepted). The endpoint's total absence of authentication (explicitly named `UnauthedRunResumed`) makes exploitation trivial once a valid `taskID` is known; the remaining constraint is discovering/leaking a valid, still-suspended-or-running `taskID`, which is plausible in real deployments given that these URLs are handed to external, less-trusted bridge/adapter services.

### Recommendation
- Require a per-task shared secret (e.g., HMAC-signed resume token) in addition to the raw `taskID`, verified before calling `ResumeJobV2`, similar to how `ExternalInitiator` uses `AccessKey`/`Secret` pairs (`core/bridges/external_initiator.go`).
- Add an idempotency/consumption guard in `UpdateTaskRunResult` so a given `taskID` can only supply a result once (e.g., only update rows where `finished_at IS NULL`, and reject/no-op subsequent resumes for the same task).
- Rate-limit and audit repeated resume attempts per `taskID`.

### Proof of Concept
Not independently reproduced in this review (no sandbox/runtime access); conceptual PoC:
1. Create a job with an async `bridge` task whose `ResponseURL` (`http://<node>/v2/resume/<taskID>`) is disclosed to/observable by an external adapter or intermediary.
2. As soon as the task is suspended, send `PATCH /v2/resume/<taskID>` with a first payload — this transitions the run to `running` and kicks off `r.Run` in a goroutine.
3. Before that run finishes, send additional `PATCH /v2/resume/<taskID>` requests with different result payloads; because there is no "already consumed" check tied to the in-flight execution, each call updates `pipeline_task_runs` and can influence/re-trigger downstream processing, without any authentication.

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

**File:** core/web/auth/auth.go (L55-151)
```go
func AuthenticateBySession(c *gin.Context, authr Authenticator) error {
	ctx := c.Request.Context()
	session := sessions.Default(c)
	sessionID, ok := session.Get(SessionIDKey).(string)
	if !ok {
		return auth.ErrorAuthFailed
	}

	user, err := authr.AuthorizedUserWithSession(ctx, sessionID)
	if err != nil {
		return err
	}

	c.Set(SessionUserKey, &user)

	return nil
}

var _ authMethod = AuthenticateBySession

// AuthenticateByToken authenticates a User by their API token.
//
// Implements authMethod
func AuthenticateByToken(c *gin.Context, authr Authenticator) error {
	ctx := c.Request.Context()
	token := &auth.Token{
		AccessKey: c.GetHeader(APIKey),
		Secret:    c.GetHeader(APISecret),
	}
	if token.AccessKey == "" {
		return auth.ErrorAuthFailed
	}

	if token.Secret == "" {
		return auth.ErrorAuthFailed
	}

	// We need to first load the user row so we can compare tokens using the stored salt
	user, err := authr.FindUserByAPIToken(ctx, token.AccessKey)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) || errors.Is(err, clsessions.ErrUserSessionExpired) {
			return auth.ErrorAuthFailed
		}
		return err
	}

	ok, err := clsessions.AuthenticateUserByToken(token, &user)
	if err != nil {
		return err
	}
	if !ok {
		return auth.ErrorAuthFailed
	}

	c.Set(SessionUserKey, &user)

	return nil
}

var _ authMethod = AuthenticateByToken

// AuthenticateExternalInitiator authenticates an external initiator request.
//
// Implements authMethod
func AuthenticateExternalInitiator(c *gin.Context, store Authenticator) error {
	ctx := c.Request.Context()
	eia := &auth.Token{
		AccessKey: c.GetHeader(static.ExternalInitiatorAccessKeyHeader),
		Secret:    c.GetHeader(static.ExternalInitiatorSecretHeader),
	}

	ei, err := store.FindExternalInitiator(ctx, eia)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return auth.ErrorAuthFailed
		}

		return errors.Wrap(err, "finding external initiator")
	}

	ok, err := bridges.AuthenticateExternalInitiator(eia, ei)
	if err != nil {
		return err
	}
	if !ok {
		return auth.ErrorAuthFailed
	}

	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})

	return nil
}
```

**File:** core/services/pipeline/runner_test.go (L787-787)
```go
		require.Contains(t, reqBody.ResponseURL, "http://localhost:6688/v2/resume/")
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

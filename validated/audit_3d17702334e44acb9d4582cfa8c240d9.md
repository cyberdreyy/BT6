Found a concrete analog: an unauthenticated endpoint that lets any external caller inject/override arbitrary "price"-like results into an in-flight job pipeline run, directly analogous to `sfnProcessTradeDispute`'s unrestricted price-override capability.

### Title
Unauthenticated `/v2/resume/:runID` endpoint allows any unprivileged caller to inject arbitrary task results into pipeline runs - (File: core/web/router.go, core/web/pipeline_runs_controller.go)

### Summary
The route `PATCH /v2/resume/:runID` is registered on the `unauthedv2` group with no authentication or authorization middleware at all, unlike every other mutating endpoint in the router which requires at minimum `RequiresRunRole`/`RequiresEditRole`/`RequiresAdminRole`. It maps to `PipelineRunsController.Resume`, which decodes an attacker-supplied JSON body directly into a `pipeline.ResumeRequest`, converts it to a `pipeline.Result` (arbitrary `Value`/`Error`), and feeds it straight into `Application.ResumeJobV2` → `pipeline.Runner.ResumeRun` → `orm.UpdateTaskRunResult`, which unconditionally overwrites the pending task's `output`/`error` for the given `taskID` (a UUID) and resumes the run.

### Finding Description
`v2Routes` explicitly separates authenticated and unauthenticated route groups: [1](#0-0) 

`unauthedv2.PATCH("/resume/:runID", prc.Resume)` is bound with zero auth middleware, in contrast to `userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))` which at least requires the "run" role (satisfiable by an external initiator or user token): [2](#0-1) 

`Resume` parses the `runID` (task UUID) from the URL and the result body from the raw, unauthenticated request, then calls `ResumeJobV2` and immediately returns 200: [3](#0-2) 

This flows into `ResumeRun`, which looks the task up purely by its UUID and unconditionally applies the caller-supplied value/error to `UpdateTaskRunResult`, restarting the run if it was suspended: [4](#0-3) 

`UpdateTaskRunResult` writes the attacker-controlled `output`/`error` directly into `pipeline_task_runs` and flips the run back to `running`, with no validation that the caller is the same bridge/adapter that originally received the async request: [5](#0-4) 

The task UUID is embedded in the outbound bridge request as the `ResponseURL` (`/v2/resume/<uuid>`), so any party that can observe or guess that UUID — including a compromised/malicious downstream HTTP server the node called, or anyone who intercepts the callback URL — can POST a fabricated result before (or instead of) the legitimate adapter response: [6](#0-5) 

This is architecturally the same class of bug as `sfnProcessTradeDispute`: a state-mutating function capable of overriding a value that downstream logic treats as authoritative (there: dispute price; here: async task/bridge result) is reachable with no access control, only relying on knowledge of an identifier (there: `vaultAddress`/`timestamp`; here: task UUID).

### Impact Explanation
An unprivileged actor who can obtain or guess a pending task's UUID can forge the outcome of any in-flight asynchronous pipeline task (e.g., bridge/external adapter results feeding price feeds, VRF, or other job pipelines), causing the node to act on falsified data and resume execution with attacker-chosen values/errors. Because `pipeline_task_runs.output`/`error` feed subsequent pipeline stages (potentially including on-chain transaction submission tasks), this can lead to submission of manipulated data on-chain or denial of legitimate task completion — a direct analog to the "override price observation" abuse described in the CEGA report.

### Likelihood Explanation
Exploitability depends on the caller learning the task UUID. This is materially reachable in normal operation because the UUID is transmitted in cleartext as `ResponseURL` to the external bridge/adapter for every async bridge task; any adapter operator, network intermediary that can see that outbound request, or attacker who compromises/impersonates the adapter endpoint can capture it and then race or replace the legitimate resume call over the fully unauthenticated endpoint. No credentials, session, or role are required to call this endpoint — this matches the "unprivileged client request" requirement.

### Recommendation
Require authentication on `/v2/resume/:runID` at minimum equivalent to `auth.RequiresRunRole`, or replace the UUID-only trust model with a per-request unguessable/rotating secret bound to the specific pending bridge callback (validated against a stored secret at resume time), and reject resumes for tasks not in the expected pending/async bridge state. This mirrors adding `onlyCegaAdmin`-equivalent access control to the vulnerable override entry point.

### Proof of Concept
1. Configure a job with an async `BridgeTask` pointing to an attacker-influenced or observed adapter; the node sends a request whose `responseURL` is `http(s)://<node>/v2/resume/<taskUUID>` as shown in `task.bridge_test.go`.
2. Before (or instead of) the legitimate adapter, an unauthenticated attacker who has obtained `<taskUUID>` issues:
   `PATCH /v2/resume/<taskUUID>` with body `{"error": null, "value": "<attacker-chosen result>"}`.
3. `PipelineRunsController.Resume` accepts this with no auth check, calls `ResumeJobV2`/`ResumeRun`, which overwrites the task's `output` in `pipeline_task_runs` and resumes the pipeline run with the forged value, exactly as `UpdateTaskRunResult` implements.

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

**File:** core/web/router.go (L449-457)
```go
	ping := PingController{app}
	userOrEI := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateExternalInitiator,
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	userOrEI.GET("/ping", ping.Show)
	userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))
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

**File:** core/services/pipeline/task.bridge_test.go (L600-609)
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

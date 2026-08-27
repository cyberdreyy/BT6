### Title
Unauthenticated pipeline run resume endpoint allows arbitrary task-result injection when a task UUID is known - (File: `core/web/pipeline_runs_controller.go`)

### Summary
The external report describes a bug class where a state-changing operation that is supposed to be gated by an unguessable/authorized value (an implicit access-control token) is instead reachable by any unprivileged caller, allowing fund/reward manipulation through repeated unauthorized calls. The closest reachable analog in this repository is the `PATCH /v2/resume/:runID` route, which is deliberately mounted with **no authentication middleware at all** and lets any network caller inject an arbitrary success/error result into a suspended pipeline task run, as long as they know (or can obtain) the task's UUID.

### Finding Description
In `core/web/router.go`, the resume route is registered on the unauthenticated router group: [1](#0-0) 

```go
func v2Routes(app chainlink.Application, r *gin.RouterGroup) {
	unauthedv2 := r.Group("/v2")

	prc := PipelineRunsController{app}
	psec := PipelineJobSpecErrorsController{app}
	unauthedv2.PATCH("/resume/:runID", prc.Resume)
```

Unlike every other stateful endpoint in `v2Routes` (jobs, bridges, external initiators, transfers, keys, etc.), which are wrapped in `auth.Authenticate(...)` with role checks (`auth.RequiresAdminRole`, `auth.RequiresEditRole`, `auth.RequiresRunRole`), this route has **zero authentication or authorization**: no session cookie, no API token, no external-initiator access key/secret pair are required.

The handler itself performs no additional access check beyond parsing the UUID from the URL and the JSON body: [2](#0-1) 

It parses `runID` as a UUID (the pipeline task-run ID) and directly calls `prc.App.ResumeJobV2(ctx, taskID, result)`, which flows to `runner.ResumeRun` → `orm.UpdateTaskRunResult`: [3](#0-2) [4](#0-3) 

`UpdateTaskRunResult` only checks that a `pipeline_task_runs` row with that ID exists and belongs to a run in `running`/`suspended` state — it performs no ownership check, no secret/token comparison, and no rate limiting beyond the generic gin rate limiter applied to the whole router group. Anyone who obtains or guesses a pending task UUID can supply an arbitrary `value` or `error` in the JSON body (`pipeline.ResumeRequest`) to complete that task with attacker-controlled data: [5](#0-4) 

The audit log entry is literally named `audit.UnauthedRunResumed`, confirming the developers were aware this endpoint is intentionally callable without authentication, relying solely on the secrecy of the task UUID as the access-control mechanism — structurally the same "implicit authorization via an internal identifier" pattern that failed in the reported `GaugeFactoryCL` case, where nothing enforced that only authorized code paths could trigger the sensitive state transition.

### Impact Explanation
Pipeline runs of type "async"/"bridge" (e.g. price-feed bridge adapters that call back into the node) suspend a task and later resume it via this exact endpoint with the task's UUID, which is handed out to the external adapter as the resume "credential." If:
- the UUID is disclosed via logs, error messages, network capture, or a compromised/malicious external adapter,
- or is otherwise obtainable/enumerable by an unprivileged party,

then that party can inject a fabricated `value` (or force an `error`) into the pipeline task, directly corrupting the observation data used downstream by the job (e.g., price data feeding an OCR/Direct Request job), potentially causing incorrect on-chain reports or answers. Because there is no authentication whatsoever on this route (contrasted with the external-initiator "run" role required to trigger *new* runs via `POST /v2/jobs/:ID/runs`), this is a genuine unauthenticated-request-impersonation issue affecting job/pipeline execution integrity — the closest concrete equivalent to "unauthorized fund/state movement" reachable from an unprivileged client in this codebase.

### Likelihood Explanation
Likelihood is moderate to low: exploitation requires the attacker to already know (or successfully guess) a valid, currently-suspended task-run UUID (a v4 UUID, 122 bits of entropy), which is not enumerable through any other unauthenticated endpoint I found. However, this UUID is the *sole* protection mechanism, is passed to external bridge adapters/off-node parties by design, and if it leaks through logs, referrers, adapter compromise, or network monitoring, exploitation is trivial and requires no credentials of any kind — this is materially weaker than every comparable state-mutating v2 route, all of which require session/token auth plus role checks.

### Recommendation
Require authentication (API token or external-initiator credentials, matching how `POST /v2/jobs/:ID/runs` is gated by `auth.RequiresRunRole`) for `PATCH /v2/resume/:runID`, or at minimum bind the resume request to a per-task secret/HMAC distinct from the identifier itself so leaking the task UUID alone is insufficient to forge a resume request. Additionally, ensure UUIDs used as resume tokens are never logged or exposed in ways that unprivileged parties could intercept.

### Proof of Concept
1. Create/observe a job with an async (e.g., bridge) task type that suspends pending an external callback (as exercised in `Test_PipelineRunner_AsyncJob_Basic`, `core/services/pipeline/runner_test.go:713-771`).
2. Obtain the suspended task's UUID (`pipeline_task_runs.id`) — e.g., via bridge adapter callback URL, logging, or interception, since it is handed to the external adapter as the resume token.
3. As an unauthenticated network client, send:
```
PATCH /v2/resume/<task-uuid>
Content-Type: application/json

{"value": "<attacker-controlled-json>"}
```
No `X-Chainlink-EA-AccessKey`/`Secret`, session cookie, or API token header is required — the route is mounted on `unauthedv2` in [1](#0-0)  and the handler in [6](#0-5)  performs no further authorization check.
4. `ResumeJobV2` → `ResumeRun` → `UpdateTaskRunResult` completes the task with the attacker-supplied value, resuming the pipeline run with forged data and marking it `RunStatusRunning`/eventually `RunStatusCompleted`.

### Citations

**File:** core/web/router.go (L238-243)
```go
func v2Routes(app chainlink.Application, r *gin.RouterGroup) {
	unauthedv2 := r.Group("/v2")

	prc := PipelineRunsController{app}
	psec := PipelineJobSpecErrorsController{app}
	unauthedv2.PATCH("/resume/:runID", prc.Resume)
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

**File:** core/services/pipeline/models.go (L250-266)
```go
type ResumeRequest struct {
	Error null.String     `json:"error"`
	Value json.RawMessage `json:"value"`
}

func (rr ResumeRequest) ToResult() (Result, error) {
	var res Result
	if rr.Error.Valid && rr.Value == nil {
		res.Error = errors.New(rr.Error.ValueOrZero())
		return res, nil
	}
	if !rr.Error.Valid && rr.Value != nil {
		res.Value = []byte(rr.Value)
		return res, nil
	}
	return Result{}, errors.New("must provide only one of either 'value' or 'error' key")
}
```

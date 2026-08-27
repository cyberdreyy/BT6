### Title
IDOR in `PipelineRunsController.Show` allows any authenticated view/run-role user to read another job's pipeline run (including task inputs/outputs) by guessing/enumerating `runID` - ([File: core/web/pipeline_runs_controller.go])

### Summary
`PipelineRunsController.Show` parses only `c.Param("runID")` and calls `prc.App.PipelineORM().FindRun(ctx, pipelineRun.ID)` — it never validates that the fetched run's `JobID` matches the `:ID` path segment of the job the caller is authorized/expected to be viewing, nor does it perform any per-job or per-owner authorization check. Because `runID` is a database-generated integer/UUID that is not scoped or re-checked against caller identity, any session-authenticated user (view or run role) can substitute an arbitrary `runID` and retrieve the full run resource — including task-run inputs/outputs — for a job they do not own.

### Finding Description
The handler is:
```go
func (prc *PipelineRunsController) Show(c *gin.Context) {
	ctx := c.Request.Context()
	pipelineRun := pipeline.Run{}
	err := pipelineRun.SetID(c.Param("runID"))
	...
	pipelineRun, err = prc.App.PipelineORM().FindRun(ctx, pipelineRun.ID)
	...
	res := presenters.NewPipelineRunResource(pipelineRun, prc.App.GetLogger())
	jsonAPIResponse(c, res, "pipelineRun")
}
``` [1](#0-0) 

Note that `c.Param("ID")` (the job ID segment of `GET /v2/jobs/:ID/runs/:runID`) is read and validated in `Index` and `Create` [2](#0-1) [3](#0-2)  but is **never referenced in `Show`**. `Show` trusts `runID` alone to select and return the run, with no cross-check that the run's job matches `:ID`, and no ownership/ACL check comparing the run's job to the authenticated session's permitted jobs. The gin/session auth middleware only verifies that the caller holds a valid session with sufficient role (view/run/admin) to call the route at all — it does not perform per-resource authorization, since the Chainlink node's role model (`Admin`/`Edit`/`Run`/`View`) is a flat, node-wide RBAC model rather than a per-job ownership model. Consequently, once any user has *any* view-capable session, they can iterate `runID` values (sequential integers) and retrieve run resources — including `pipeline.TaskRun` inputs/outputs, which may embed upstream request payloads (e.g., a Functions request's arguments/secrets references) — for jobs unrelated to whatever job the attacker believes they are scoped to.

### Impact Explanation
This is a broken object-level authorization (IDOR) issue: a low-privileged, view/run-role authenticated user can disclose sensitive task-run inputs/outputs belonging to a different job/subscriber by simply changing the numeric `runID` in the URL, since the code path performs no ownership check tying the run back to the caller or to the `:ID` job segment. This matches the "cross-user response confusion" / unauthorized disclosure of run inputs/outputs impact class in scope.

### Likelihood Explanation
Exploitation requires only a valid, already-authenticated session with any view-or-higher role (the minimal role needed to hit `GET /v2/jobs/:ID/runs/:runID`) and knowledge/guessing of another job's `runID`, which is a small monotonically increasing integer and thus trivially enumerable. No additional privilege, admin access, or database access is needed, making this fully repeatable and low-effort for any unprivileged-but-authenticated caller.

### Recommendation
In `PipelineRunsController.Show`, after fetching the run, verify that `pipelineRun.PipelineSpec.JobID` (or equivalent job reference) matches the `:ID` path parameter, returning `404`/`403` on mismatch; additionally consider scoping `FindRun` queries by job ID at the ORM layer (`FindRun(ctx, jobID, runID)`) so cross-job lookups are impossible at the SQL layer rather than relying on application-level filtering alone.

### Proof of Concept
1. Create Job A and Job B via the jobs API, each producing at least one pipeline run (e.g., via webhook trigger), noting Job B's resulting `runID`.
2. Authenticate as a session user with only "view" role scoped conceptually to Job A.
3. Call `GET /v2/jobs/{JobA.ID}/runs/{JobB.runID}`.
4. Assert current behavior: HTTP 200 with the full `pipelineRun` JSON:API resource for Job B, including `taskRuns[].output`/`input` fields.
5. Expected behavior after fix: HTTP 404/403, with no run data returned when `runID` does not belong to the job referenced by `:ID`.

### Citations

**File:** core/web/pipeline_runs_controller.go (L29-53)
```go
func (prc *PipelineRunsController) Index(c *gin.Context, size, page, offset int) {
	id := c.Param("ID")

	// Temporary: if no size is passed in, use a large page size. Remove once frontend can handle pagination
	if c.Query("size") == "" {
		size = 1000
	}

	var pipelineRuns []pipeline.Run
	var count int
	var err error

	ctx := c.Request.Context()
	if id == "" {
		pipelineRuns, count, err = prc.App.JobORM().PipelineRuns(ctx, nil, offset, size)
	} else {
		jobSpec := job.Job{}
		err = jobSpec.SetID(c.Param("ID"))
		if err != nil {
			jsonAPIError(c, http.StatusUnprocessableEntity, err)
			return
		}

		pipelineRuns, count, err = prc.App.JobORM().PipelineRuns(ctx, &jobSpec.ID, offset, size)
	}
```

**File:** core/web/pipeline_runs_controller.go (L67-84)
```go
func (prc *PipelineRunsController) Show(c *gin.Context) {
	ctx := c.Request.Context()
	pipelineRun := pipeline.Run{}
	err := pipelineRun.SetID(c.Param("runID"))
	if err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}

	pipelineRun, err = prc.App.PipelineORM().FindRun(ctx, pipelineRun.ID)
	if err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	res := presenters.NewPipelineRunResource(pipelineRun, prc.App.GetLogger())
	jsonAPIResponse(c, res, "pipelineRun")
}
```

**File:** core/web/pipeline_runs_controller.go (L100-125)
```go

	idStr := c.Param("ID")

	// Webhook runs used external job UUIDs; that job type has been removed.
	if _, err := uuid.Parse(idStr); err == nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, fmt.Errorf("cannot run job of type %q: %w", job.Webhook, job.ErrJobTypeRemoved))
		return
	}

	_, isUser := auth.GetAuthenticatedUser(c)
	// only users are allowed to run jobs using int IDs - EIs not allowed
	if isUser {
		// Is it an int32? Then process it regardless of type
		var jobID int32
		jobID64, err := strconv.ParseInt(idStr, 10, 32)
		if err == nil {
			jobID = int32(jobID64)
			jobRunID, err := prc.App.RunJobV2(ctx, jobID, nil)
			if err != nil {
				jsonAPIError(c, http.StatusInternalServerError, err)
				return
			}
			respondWithPipelineRun(jobRunID)
			return
		}
	}
```

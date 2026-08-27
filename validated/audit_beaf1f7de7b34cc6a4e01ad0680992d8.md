### Title
Missing job-ownership scoping in `PipelineRunsController.Index` allows platform-wide pipeline run disclosure via GET /v2/pipeline/runs - ([File: core/web/pipeline_runs_controller.go])

### Summary
`PipelineRunsController.Index` branches on the `:ID` route parameter; when it is empty (i.e., the request hits `GET /v2/pipeline/runs` rather than `GET /v2/jobs/:ID/runs`), it calls `prc.App.JobORM().PipelineRuns(ctx, nil, offset, size)` with a `nil` job filter. This returns pipeline run records for every job configured on the node, with no per-caller or per-job authorization narrowing, and returns them through the standard authenticated-session/API-token middleware rather than an admin-only check.

### Finding Description
In `Index` <cite repo="EzraCole/chainlink--005" path="core/web/pipeline_runs_controller.go" start="29,42,43" end="29,42,43" />:
```go
id := c.Param("ID")
...
if id == "" {
    pipelineRuns, count, err = prc.App.JobORM().PipelineRuns(ctx, nil, offset, size)
} else {
    ...
    pipelineRuns, count, err = prc.App.JobORM().PipelineRuns(ctx, &jobSpec.ID, offset, size)
}
```
When `id` is empty, the job-ID filter passed to `PipelineRuns` is `nil`, which the ORM interprets as "no filter" and returns runs across all jobs in the `pipeline_runs` table, not scoped to any specific job or requester. The result set is then serialized via `presenters.NewPipelineRunResources` and returned as a paginated JSON:API response — including task-level inputs/outputs/errors captured for every run, without regard to which job or bridge the caller is authorized to interact with. Unlike `Create`/`Resume`, which enforce job-ID-specific access via `RunJobV2`/`ResumeJobV2` scoped to a single job, `Index` has no per-job authorization check at all — it relies solely on whatever role gate is applied at route-registration time in `core/web/router.go`, and does not perform any additional filtering based on the authenticated principal's role or job ownership.

### Impact Explanation
Any caller whose credentials satisfy the role required for this route (the lowest role permitted for job-run endpoints, distinct from admin) can enumerate the run history of every job on the node by simply omitting the `:ID` path segment (`GET /v2/pipeline/runs`), rather than being restricted to `GET /v2/jobs/:ID/runs` for jobs they are meant to access. This exposes pipeline run inputs, outputs, and error details platform-wide — potentially including bridge responses, computed values, and other run-embedded data — to a caller who should not have visibility beyond the route's intended scope. This matches an authorization-exactness / unauthorized cross-job data disclosure impact class.

### Likelihood Explanation
The precondition is simply holding any credential (session or API token) that passes the authentication/role middleware attached to `GET /v2/pipeline/runs` or `GET /v2/jobs/:ID/runs` — no elevated privilege, job ownership, or additional secret is required beyond that baseline role. Triggering it requires only a single unauthenticated-in-effect GET request with no `:ID` parameter, making it trivially repeatable and requiring no race conditions or timing.

### Recommendation
Scope `Index` so that omitting `:ID` either (a) is rejected with a 4xx error, since this controller is documented as "returns all pipeline runs for a job," or (b) if the platform-wide listing is an intentional feature, gate it behind the same role check applied to admin-level global data endpoints, and additionally filter results by the jobs the authenticated principal is authorized to view rather than passing `nil` unconditionally to `JobORM().PipelineRuns`.

### Proof of Concept
1. In `core/web/pipeline_runs_controller_test.go`, add a table-driven integration test that creates two distinct jobs (JobA owned/associated with test fixture A, JobB with fixture B) and creates pipeline runs for each.
2. Authenticate the test HTTP client using the minimum role required by the route (not admin).
3. Issue `GET /v2/pipeline/runs` with no `:ID`.
4. Assert that the response currently contains runs from both JobA and JobB (demonstrating the disclosure), then after the fix assert the response is either rejected or scoped to only the caller's authorized job(s).
5. Contrast with `GET /v2/jobs/:ID/runs` for JobA, confirming that endpoint is correctly scoped, to highlight the asymmetry that the unscoped route bypasses expected job-level scoping.
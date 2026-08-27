### No vulnerability found for this question.

**Analysis basis:**

`PipelineRunsController.Create` only performs input validation (rejects UUID/webhook job IDs, requires the caller be a "user" per `auth.GetAuthenticatedUser`) and then calls `prc.App.RunJobV2(ctx, jobID, nil)` directly with the caller-supplied integer job ID [1](#0-0) . `RunJobV2` in turn fetches the job purely by `jobID` via `app.jobORM.FindJob(ctx, jobID)` with no reference to the authenticated user at all [2](#0-1) . Route-level protection is `auth.RequiresRunRole` (confirmed by the middleware definitions in `core/web/auth/auth.go` gating on `user.Role`) [3](#0-2) , which is indeed purely role-based, not job-scoped.

However, this is not a security bug: Chainlink node users (`UserRoleView`/`UserRoleRun`/`UserRoleEdit`/`UserRoleAdmin`) are **node-level administrative roles**, not per-job tenants. The node's `job.Job` resources are global to the node — there is no ownership/ACL field associating a job with a specific user anywhere in the `job.Job` model or job ORM. Any credential with `run` role or higher is, by design, trusted to trigger pipeline runs for **any** job configured on that node; this mirrors the same design used by the GraphQL `RunJob` resolver, which likewise only calls `authenticateUserCanRun` (role check) before calling `RunJobV2` with no job-ownership check [4](#0-3) . External initiators are separately restricted from using this integer-ID path (`isUser` check excludes EI-authenticated requests) [5](#0-4) .

Since Chainlink nodes are single-tenant systems where all authenticated dashboard/API-key users are trusted operators of that node (differentiated only by capability tier: view/run/edit/admin), there is no "job ownership" invariant to violate — role-based gating without job-level ACL is the intended authorization model here, not a missing-check flaw. The premise of the question (that job-level ACL should exist) does not match Chainlink's threat model or resource ownership design in this codebase.

### Citations

**File:** core/web/pipeline_runs_controller.go (L109-124)
```go
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
```

**File:** core/services/chainlink/application.go (L1126-1137)
```go
func (app *ChainlinkApplication) RunJobV2(
	ctx context.Context,
	jobID int32,
	meta map[string]any,
) (int64, error) {
	if build.IsProd() {
		return 0, errors.New("manual job runs not supported on secure builds")
	}
	jb, err := app.jobORM.FindJob(ctx, jobID)
	if err != nil {
		return 0, errors.Wrapf(err, "job ID %v", jobID)
	}
```

**File:** core/web/auth/auth.go (L200-217)
```go
// RequiresRunRole extracts the user object from the context, and asserts the user's role is at least
// 'run'
func RequiresRunRole(handler func(*gin.Context)) func(*gin.Context) {
	return func(c *gin.Context) {
		user, ok := GetAuthenticatedUser(c)
		if !ok {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("not a valid session"))
			return
		}
		if user.Role == clsessions.UserRoleView {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("Unauthorized"))
			return
		}
		handler(c)
	}
}
```

**File:** core/web/resolver/mutation.go (L1213-1228)
```go
func (r *Resolver) RunJob(ctx context.Context, args struct {
	ID graphql.ID
}) (*RunJobPayloadResolver, error) {
	if err := authenticateUserCanRun(ctx); err != nil {
		return nil, err
	}

	jobID, err := stringutils.ToInt32(string(args.ID))
	if err != nil {
		return nil, err
	}

	jobRunID, err := r.App.RunJobV2(ctx, jobID, nil)
	if err != nil {
		return nil, err
	}
```

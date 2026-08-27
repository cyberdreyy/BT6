### Title
External-Initiator credentials bypass the "EIs not allowed" check and trigger arbitrary job runs by numeric job ID - ([File: core/web/pipeline_runs_controller.go])

### Summary
`auth.AuthenticateExternalInitiator` stores the authenticated user as a generic `*clsessions.User{Role: UserRoleRun}` under the same `SessionUserKey` used for real user sessions/tokens [1](#0-0) . `PipelineRunsController.Create` uses `auth.GetAuthenticatedUser(c)` to gate the numeric-job-ID run path with the comment "only users are allowed to run jobs using int IDs - EIs not allowed" [2](#0-1) , but because EI-authenticated requests also populate `SessionUserKey`, `isUser` evaluates `true` for EI holders too, so the intended EI exclusion never triggers. No code anywhere in `Create` checks the authenticated external initiator (`auth.GetAuthenticatedExternalInitiator`) against the target job's configured EI, or against the job's ownership at all.

### Finding Description
The route `POST /v2/jobs/:ID/runs` is mounted with `auth.RequiresRunRole(prc.Create)` behind the combined middleware chain `AuthenticateExternalInitiator, AuthenticateByToken, AuthenticateBySession` [3](#0-2) .

When a request supplies valid `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers for EI credential #1, `AuthenticateExternalInitiator` looks up the EI by access key, verifies the secret, and then unconditionally sets both:
- `SessionExternalInitiatorKey` → the found `ExternalInitiator` record, and
- `SessionUserKey` → `&clsessions.User{Role: clsessions.UserRoleRun}` [1](#0-0) 

`RequiresRunRole` only checks that a `*clsessions.User` exists and its role isn't `View` [4](#0-3) , so the EI-derived pseudo-user passes.

Inside `Create`, the intended safeguard is:
```go
_, isUser := auth.GetAuthenticatedUser(c)
if isUser {
    ...
    jobRunID, err := prc.App.RunJobV2(ctx, jobID, nil)
    ...
}
``` [5](#0-4) 

`GetAuthenticatedUser` only checks for the presence of a `*clsessions.User` value under `SessionUserKey` [6](#0-5) . Because `AuthenticateExternalInitiator` stores exactly that type under that same key, `isUser` is `true` for EI-authenticated requests, defeating the comment's stated intent ("EIs not allowed"). The code then parses `idStr` as an `int32` job ID and calls `prc.App.RunJobV2(ctx, jobID, nil)` directly, with **no comparison whatsoever** between the authenticated EI (obtainable via `auth.GetAuthenticatedExternalInitiator(c)`) and the target job's own configured external initiator, and no check that the job even has any external-initiator trigger configured. Any job with a numeric ID reachable by `RunJobV2` can be triggered — not just jobs bound to the requester's own EI, but any job in the node.

The only exclusion actually enforced is for UUID-form job IDs (legacy webhook jobs), which are rejected upfront as removed functionality [7](#0-6) . That check does not restore the missing EI/job binding for the int32 path.

### Impact Explanation
Any holder of valid access-key/secret credentials for a single External Initiator can trigger execution of an arbitrary job on the node by numeric ID via `POST /v2/jobs/:ID/runs`, regardless of which EI (if any) that job is configured to accept runs from. This is unauthorized job execution / cross-EI (and in fact cross-tenant) job-run impersonation — matching the Chainlink bounty "unauthorized job run" impact class. Depending on job pipeline contents (e.g., jobs that submit on-chain transactions, call external adapters with side effects, or consume rate-limited/paid resources), this can cause unintended fund movement, resource exhaustion, or unauthorized interaction with third-party systems on the node operator's behalf.

### Likelihood Explanation
Preconditions are minimal: the attacker only needs one valid EI access key + secret pair (the lowest trust credential type explicitly designed only to trigger its own webhook runs). No other role or admin access is required. The exploit is a single unauthenticated-relative-to-other-EIs HTTP POST with guessable/enumerable small integer job IDs, and is fully repeatable (stateless per-request auth, no additional binding check exists to defeat).

### Recommendation
- In `PipelineRunsController.Create`, distinguish EI-derived pseudo-users from real authenticated users — e.g., check `auth.GetAuthenticatedExternalInitiator(c)` first and reject (or separately validate) EI-authenticated requests, rather than relying on `GetAuthenticatedUser` returning a `*clsessions.User` as a proxy for "is a real user."
- If external-initiator-triggered runs by numeric job ID are still meant to be supported for jobs explicitly configured with `externalInitiators`, add an explicit check that the EI record returned by `GetAuthenticatedExternalInitiator` matches an EI configured on the target job before calling `RunJobV2`.
- Consider using distinct context keys/types for "real user" sessions vs. "external initiator" pseudo-sessions so type assertions in `GetAuthenticatedUser` can't be confused.

### Proof of Concept
Handler-level integration test:
1. Create two External Initiators, EI1 and EI2, via the ORM/`CreateExternalInitiatorViaWeb`.
2. Create Job A (any non-webhook job type, e.g., cron or DR job with a numeric ID) that is not associated with EI1 or EI2.
3. Authenticate as EI1 (set `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers for EI1) and send `POST /v2/jobs/{JobA.ID}/runs`.
4. Assert:
   - Current (buggy) behavior: response is `200 OK`/successful, and a `pipeline_runs` row is created for Job A (confirm via `App.PipelineORM().FindRun`), proving EI1 triggered a job it has no relationship to.
   - Expected (fixed) behavior: response should be `401`/`403` because `GetAuthenticatedExternalInitiator(c)` returns EI1, and EI1 is not configured on Job A, so the handler must refuse to call `RunJobV2`.
5. Additional unit test on `PipelineRunsController.Create` directly (mocking `app.RunJobV2`) asserting it is never invoked when the authenticated principal is an external initiator not bound to the requested job ID.

### Citations

**File:** core/web/auth/auth.go (L143-148)
```go
	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})
```

**File:** core/web/auth/auth.go (L178-187)
```go
func GetAuthenticatedUser(c *gin.Context) (*clsessions.User, bool) {
	obj, ok := c.Get(SessionUserKey)
	if !ok {
		return nil, false
	}

	user, ok := obj.(*clsessions.User)

	return user, ok
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

**File:** core/web/pipeline_runs_controller.go (L101-107)
```go
	idStr := c.Param("ID")

	// Webhook runs used external job UUIDs; that job type has been removed.
	if _, err := uuid.Parse(idStr); err == nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, fmt.Errorf("cannot run job of type %q: %w", job.Webhook, job.ErrJobTypeRemoved))
		return
	}
```

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

**File:** core/web/router.go (L450-456)
```go
	userOrEI := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateExternalInitiator,
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	userOrEI.GET("/ping", ping.Show)
	userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))
```

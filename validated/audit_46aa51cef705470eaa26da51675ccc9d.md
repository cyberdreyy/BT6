### Title
External Initiator credential can trigger pipeline runs on arbitrary jobs (not just the bound job) due to broken `isUser` check in `PipelineRunsController.Create` - ([File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` stores the authenticated EI under the *same* context key used for regular users (`SessionUserKey`), tagging it with a synthetic `User{Role: UserRoleRun}` [1](#0-0) . `PipelineRunsController.Create` uses `auth.GetAuthenticatedUser(c)` only to check presence (`isUser`), which returns `true` for an EI-authenticated request as well, even though the comment explicitly states "EIs not allowed" for this int-ID path [2](#0-1) . As a result, any EI credential that passes `RequiresRunRole` can call `prc.App.RunJobV2(ctx, jobID, nil)` for an arbitrary integer job ID taken straight from the URL `:ID`, with no verification that the job is bound to that EI (or is even a webhook job at all).

### Finding Description
The route is registered as:
```
userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))
``` [3](#0-2) 

`RequiresRunRole` only checks `user.Role != UserRoleView` — it has no notion of "this is an EI, not a real user" [4](#0-3) .

`AuthenticateExternalInitiator` sets both `SessionExternalInitiatorKey` (the real EI) and `SessionUserKey` (a fabricated `User{Role: UserRoleRun}`, with no email/ID tying it back to the EI or its bound job) [1](#0-0) .

In `Create`, the code branches on `isUser` from `auth.GetAuthenticatedUser(c)`:
```go
_, isUser := auth.GetAuthenticatedUser(c)
// only users are allowed to run jobs using int IDs - EIs not allowed
if isUser {
    ...
    jobRunID, err := prc.App.RunJobV2(ctx, jobID, nil)
    ...
}
``` [2](#0-1) 

Because `GetAuthenticatedUser` simply reads `SessionUserKey` presence (`obj, ok := c.Get(SessionUserKey)`) [5](#0-4) , and that key is populated for EI requests too, `isUser` evaluates to `true` for an EI credential — contradicting the inline comment's intent that "EIs not allowed" on this path. The `:ID` is parsed directly as `jobID int32` from the URL with no cross-check against `SessionExternalInitiatorKey`'s bound job, and `RunJobV2` is invoked unconditionally for that job ID.

### Impact Explanation
An attacker holding valid EI credentials scoped to job A can POST to `/v2/jobs/<jobB_ID>/runs` and trigger a pipeline run on job B — a job that may belong to a different bridge/spec/owner, not restricted to webhook jobs. This is an unauthorized job run / authorization bypass: it lets a low-privilege, narrowly-scoped credential (meant only to trigger its own bound webhook) execute arbitrary jobs on the node, potentially causing unwanted on-chain transactions, fund movement (e.g., VRF fulfillment or OCR-adjacent jobs that can be manually triggered), or resource abuse.

### Likelihood Explanation
The only precondition is possessing any valid EI access key/secret pair (the lowest-privilege credential type explicitly designed to be scoped to a single bound job). No admin, database, or additional roles are needed. The request is a single unauthenticated-relative-to-other-jobs POST with a guessable/enumerable integer job ID, fully repeatable.

### Recommendation
In `PipelineRunsController.Create`, explicitly check for `auth.GetAuthenticatedExternalInitiator(c)` and reject (401/403) if an EI is present rather than relying on the shared `SessionUserKey`. If EI-triggered runs are intended to be supported at all, verify that the requested job's bound `ExternalInitiatorID`/webhook spec matches the authenticated EI before calling `RunJobV2`. More robust: give EI-authenticated contexts a distinct context key (not `SessionUserKey`) so `GetAuthenticatedUser`/`isUser` cannot conflate real users with external initiators.

### Proof of Concept
Go handler-level integration test plan:
1. Create two webhook jobs, job A and job B, each with a distinct `ExternalInitiator` bound to job A only.
2. Authenticate via `AuthenticateExternalInitiator` using job A's EI access key/secret against `POST /v2/jobs/{jobB_ID}/runs`.
3. Assert current (vulnerable) behavior: response is `200/201` with a created pipeline run for job B (`respondWithPipelineRun` succeeds), proving `isUser` incorrectly evaluated true and `RunJobV2` executed for an unrelated job.
4. Expected fixed behavior: response should be `401`/`403` because the caller is an EI not bound to job B (or EIs should be entirely rejected on this path per the existing comment).
5. Add a unit test on `auth.GetAuthenticatedUser` combined with `AuthenticateExternalInitiator` demonstrating that `isUser` is `true` for EI-only authenticated contexts, contradicting the intended "EIs not allowed" semantics in `PipelineRunsController.Create`.

### Citations

**File:** core/web/auth/auth.go (L143-150)
```go
	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})

	return nil
```

**File:** core/web/auth/auth.go (L177-187)
```go
// GetAuthenticatedUser extracts the authentication user from the context.
func GetAuthenticatedUser(c *gin.Context) (*clsessions.User, bool) {
	obj, ok := c.Get(SessionUserKey)
	if !ok {
		return nil, false
	}

	user, ok := obj.(*clsessions.User)

	return user, ok
}
```

**File:** core/web/auth/auth.go (L202-217)
```go
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

**File:** core/web/pipeline_runs_controller.go (L109-125)
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
	}
```

**File:** core/web/router.go (L449-456)
```go
	ping := PingController{app}
	userOrEI := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateExternalInitiator,
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	userOrEI.GET("/ping", ping.Show)
	userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))
```

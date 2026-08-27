### Title
External Initiator credentials can trigger `RunJobV2` on arbitrary integer job IDs, bypassing the "EIs not allowed for int job IDs" restriction - ([File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` sets the same `SessionUserKey` context value used for real user sessions, giving External Initiator (EI) requests a synthetic `UserRoleRun` user. `PipelineRunsController.Create` uses `auth.GetAuthenticatedUser(c)` to decide whether the caller is a "user" allowed to run jobs by integer ID, but this check cannot distinguish a real authenticated user from an EI-authenticated request, defeating the code's own intent.

### Finding Description
The route `POST /v2/jobs/:ID/runs` is registered in `core/web/router.go` under the `userOrEI` group with multiple auth methods tried in order: `auth.AuthenticateExternalInitiator`, `auth.AuthenticateByToken`, `auth.AuthenticateBySession`, wrapped by `auth.RequiresRunRole(prc.Create)`. [1](#0-0) 

In `core/web/auth/auth.go`, `AuthenticateExternalInitiator` validates only the EI access key/secret and, on success, sets both `SessionExternalInitiatorKey` and `SessionUserKey` to a synthetic `&clsessions.User{Role: clsessions.UserRoleRun}`: [2](#0-1) 

`GetAuthenticatedUser` merely checks for the presence of `SessionUserKey` and returns it, with no way to tell whether it was populated by a real session/token or by the EI auth method: [3](#0-2) 

`RequiresRunRole` only checks `user.Role != clsessions.UserRoleView`, which the synthetic EI user (`UserRoleRun`) satisfies, so it passes through to `prc.Create`: [4](#0-3) 

Inside `PipelineRunsController.Create`, the comment "only users are allowed to run jobs using int IDs - EIs not allowed" is enforced solely via `isUser := auth.GetAuthenticatedUser(c)`, which is `true` for both real users and EI callers due to the conflated identity above. When `isUser` is true and the path param parses as an int32, `prc.App.RunJobV2(ctx, jobID, nil)` is invoked directly against that arbitrary job ID — there is no check that the job is bound to, or associated with, the EI credential used: [5](#0-4) 

Because EI credentials are typically scoped/bound to a specific webhook job by UUID (not by arbitrary integer job IDs), a low-trust EI credential holder can supply any integer job ID and successfully trigger `RunJobV2` on a job it was never intended to control, contradicting the explicit design comment.

### Impact Explanation
This allows an unprivileged External Initiator credential holder to trigger unauthorized execution of arbitrary jobs (by integer ID) on the node — including jobs that have nothing to do with the EI's registered webhook. This can cause unwanted state mutation, resource exhaustion/DoS via repeated forced runs, or on-chain side effects depending on job type (e.g., triggering transmissions/writes for a job the EI was not authorized for). This maps to Chainlink's "unauthorized job run" / authorization-bypass impact class.

### Likelihood Explanation
The only precondition is possession of a valid EI access key/secret (no real user session or API token required). The exploit is a single HTTP request (`POST /v2/jobs/<int-ID>/runs` with `OCR-EXTERNAL-INITIATOR-ACCESS-KEY`/`OCR-EXTERNAL-INITIATOR-SECRET` headers) and is fully repeatable against any integer job ID on the node, since `RunJobV2` performs no ownership/binding check between the EI and the target job.

### Recommendation
Distinguish EI-authenticated identities from real user identities instead of conflating them under `SessionUserKey`. For example, keep `SessionUserKey` unset (or use a distinct marker) for EI auth, and in `PipelineRunsController.Create` explicitly check `auth.GetAuthenticatedExternalInitiator(c)` to reject EI callers for integer job IDs, or verify the EI is bound to the specific job being run before invoking `RunJobV2`.

### Proof of Concept
Go handler-level integration test plan (extending `core/web/pipeline_runs_controller_test.go`):
1. Set up an app with an External Initiator registered (e.g. via `ExternalInitiatorsController` or DB fixture) yielding a valid `AccessKey`/`Secret`, and a separate job created via `app.AddJobV2` with an integer `jb.ID` unrelated to that EI.
2. Build an HTTP client that sends only `OCR-EXTERNAL-INITIATOR-ACCESS-KEY` and `OCR-EXTERNAL-INITIATOR-SECRET` headers (no session cookie, no `X-API-KEY`/`X-API-SECRET`).
3. POST to `/v2/jobs/<jb.ID>/runs` with this client.
4. Assert (currently failing/expected to demonstrate the bug) that the response is `201`/success and a new `pipeline.Run` is created for `jb.ID`, i.e. `prc.App.RunJobV2` was invoked — proving an EI-only credential triggered a run on a job by integer ID despite the code comment stating "EIs not allowed for int job IDs".
5. The fix should make this same test assert `401`/`422` and that no new pipeline run row exists for `jb.ID`.

### Citations

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

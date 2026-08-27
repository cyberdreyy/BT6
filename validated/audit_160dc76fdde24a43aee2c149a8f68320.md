### Title
Authentication method confusion allows External Initiators to bypass integer-ID job run restriction - ([File: core/web/pipeline_runs_controller.go])

### Summary
`PipelineRunsController.Create` attempts to prevent External Initiators (EIs) from triggering runs via integer job IDs by checking `isUser` returned from `auth.GetAuthenticatedUser(c)`. However, `AuthenticateExternalInitiator` unconditionally sets the same `SessionUserKey` context value to a synthetic `&sessions.User{Role: UserRoleRun}`, so `GetAuthenticatedUser` returns `ok=true` for EI-authenticated requests exactly as it would for a real user, completely defeating the intended restriction.

### Finding Description
The route for creating pipeline runs is authenticated via the shared `auth.Authenticate` middleware, which tries multiple `authMethod`s (session, API token, external initiator) in sequence [1](#0-0) . When an external initiator authenticates using its access key/secret headers, `AuthenticateExternalInitiator` validates the EI credentials and then stores a fabricated `*sessions.User` with `Role: UserRoleRun` under the very same `SessionUserKey` used for real user sessions/tokens: [2](#0-1) 

`GetAuthenticatedUser` merely fetches whatever object is stored at `SessionUserKey` and type-asserts it to `*clsessions.User`, returning `true` regardless of whether the value came from a genuine session/token login or from EI authentication: [3](#0-2) 

`PipelineRunsController.Create` relies on this same function to gate integer-ID job runs, under the explicit assumption that `isUser == true` implies a real user (not an EI): [4](#0-3) 

Because `isUser` is derived purely from the presence of a `*sessions.User` object in context — which `AuthenticateExternalInitiator` always injects on successful EI auth — this check cannot distinguish an EI from a genuine user. An attacker with only valid EI credentials (access key + secret) can call `POST /v2/jobs/<int-id>/runs` and have `isUser` evaluate to `true`, causing `prc.App.RunJobV2` to execute for an arbitrary job by integer ID, directly contradicting the code comment "only users are allowed to run jobs using int IDs - EIs not allowed."

### Impact Explanation
An external-initiator credential holder — a lower-trust, non-admin credential class intended only to trigger specific webhook-type job runs — can trigger pipeline runs for *any* job addressable by integer ID, not just its intended webhook job. This is an authorization/role-boundary bypass allowing unauthorized job execution, matching the "unauthorized job run" impact class in Chainlink's bounty program.

### Likelihood Explanation
The precondition is simply possessing valid EI access key/secret (a credential class explicitly designed to be less privileged than a full user session/token). No additional bypass of signature checks, session hijacking, or admin access is required — this is a straightforward, reliably reproducible logic bug triggerable with a single authenticated HTTP request.

### Recommendation
Do not overload `SessionUserKey`/`GetAuthenticatedUser` for EI authentication. `AuthenticateExternalInitiator` should either avoid setting `SessionUserKey` at all (leaving user detection strictly for session/token auth), or the code should check `GetAuthenticatedExternalInitiator(c)` first in `Create` and explicitly reject/short-circuit when an EI context is present, rather than relying on `isUser` alone to imply "not an EI."

### Proof of Concept
Handler/integration test plan:
1. Create an application, insert a job addressable by integer ID (e.g., a directrequest or ocr job), and create an `ExternalInitiator` record with known access key/secret via `bridges.NewExternalInitiator`.
2. Build an HTTP client using EI headers (`static.ExternalInitiatorAccessKeyHeader` / `static.ExternalInitiatorSecretHeader`) instead of session cookie or API token headers.
3. `POST /v2/jobs/<int-job-id>/runs` using this EI-authenticated client.
4. Assert the response status is `200 OK` with a `pipelineRun` resource returned (contradicting the intended `422 bad job ID` / rejection), and verify via `PipelineORM().FindRun` that a new pipeline run row was created for that job — proving the EI bypassed the "EIs not allowed" restriction on integer-ID runs.

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

**File:** core/web/auth/auth.go (L155-175)
```go
// Authenticate is middleware which authenticates the request by attempting to
// authenticate using all the provided methods.
func Authenticate(store Authenticator, methods ...authMethod) gin.HandlerFunc {
	return func(c *gin.Context) {
		var err error
		for _, method := range methods {
			err = method(c, store)
			if !errors.Is(err, auth.ErrorAuthFailed) {
				break
			}
		}
		if err != nil {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, err)

			return
		}

		c.Next()
	}
}
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

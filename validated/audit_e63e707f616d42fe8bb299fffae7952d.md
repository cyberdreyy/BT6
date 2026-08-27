### Title
Authorization bypass in PipelineRunsController.Create allows External Initiator credentials to run arbitrary jobs by integer ID - (File: core/web/pipeline_runs_controller.go)

### Summary
`PipelineRunsController.Create` gates integer-ID job runs with a check that is meant to exclude External Initiator (EI) callers ("only users are allowed to run jobs using int IDs - EIs not allowed"), but the gate (`auth.GetAuthenticatedUser`) also succeeds for EI-authenticated requests because `AuthenticateExternalInitiator` stores a synthetic `*clsessions.User` under the same `SessionUserKey`. This lets an EI credential holder trigger `RunJobV2` on any int32 job ID, not just the job tied to their own EI webhook spec.

### Finding Description
In `core/web/pipeline_runs_controller.go`, the `Create` handler first rejects UUID-style (webhook) IDs, then does: [1](#0-0) 
The comment explicitly states EIs should not be allowed to use this int-ID path, and the code uses `isUser` from `auth.GetAuthenticatedUser(c)` as the sole discriminator.

However, `auth.GetAuthenticatedUser` simply reads whatever object is stored at `SessionUserKey`: [2](#0-1) 

Critically, `AuthenticateExternalInitiator` — the auth method used for EI-authenticated requests — also populates `SessionUserKey` with a synthetic `*clsessions.User{Role: clsessions.UserRoleRun}`: [3](#0-2) 

Because both real user sessions/tokens (`AuthenticateBySession`, `AuthenticateByToken`) and EI authentication (`AuthenticateExternalInitiator`) populate the identical `SessionUserKey`, `GetAuthenticatedUser` cannot distinguish an EI-authenticated caller from an actual dashboard/API user. Consequently the `isUser` check in `PipelineRunsController.Create` — which the source comment says is intended specifically to block EIs — is satisfied by EI credentials as well, and the handler proceeds to call `prc.App.RunJobV2(ctx, jobID, nil)` for **any** `jobID` supplied in the URL path, with no check that the job belongs to, or was created for, that EI's registered webhook spec.

### Impact Explanation
An attacker holding only EI (external-initiator) credentials — a lower-privilege credential intended to trigger a single specific webhook job — can trigger pipeline runs of arbitrary jobs on the node by numeric job ID. This is an authorization/role bypass: EI credentials are meant to be scoped to their own webhook job, but the shared `SessionUserKey` mechanism collapses that scoping, enabling unauthorized job execution across job boundaries (potential unauthorized on-chain fund movement/state changes depending on job type). This matches Chainlink's "unauthorized job run"/authorization bypass impact class.

### Likelihood Explanation
The precondition is possession of any valid EI access key/secret pair (headers per `static.ExternalInitiatorAccessKeyHeader`/`static.ExternalInitiatorSecretHeader`), which is a lower-trust credential than a full user session/API token. No other privilege is required, and the exploit is a single unauthenticated-as-user HTTP POST to `/v2/jobs/<int-job-id>/runs` reusing EI headers. This is trivially repeatable given a valid EI credential — feasibility is high, though it depends on the router actually exposing this route to `AuthenticateExternalInitiator` as an accepted auth method (this repo snapshot's route-wiring file could not be located/verified in this session, so it is not 100% confirmed that this exact route accepts EI auth in the current build, though the explicit "EIs not allowed" comment and the isUser gate's existence strongly imply EI callers do reach this handler in normal routing).

### Recommendation
Do not rely on `GetAuthenticatedUser`/`SessionUserKey` to distinguish EI callers from real users. Use `GetAuthenticatedExternalInitiator(c)` explicitly to detect EI-authenticated requests and reject the int-ID path for them (`ok` from `GetAuthenticatedExternalInitiator` should short-circuit with 401/403 before checking `isUser`). Additionally, avoid storing a synthetic `*clsessions.User` under the same `SessionUserKey` used for genuine user auth — use a distinct marker/type or an explicit `IsExternalInitiator` flag on the context so all downstream role checks can differentiate the two authentication mechanisms unambiguously.

### Proof of Concept
Go handler-level integration test plan (in `core/web/pipeline_runs_controller_test.go` style):
1. Set up an app with a webhook job `J1` bound to `EI-A`, and a second, unrelated job `J2` (e.g., a Flux Monitor or another webhook job) not associated with `EI-A`.
2. Register `EI-A` and obtain its access key/secret.
3. Send `POST /v2/jobs/<J2-ID>/runs` (integer ID) with headers `static.ExternalInitiatorAccessKeyHeader`/`Secret` set to `EI-A`'s credentials only (no session cookie, no `X-API-KEY`/`X-API-SECRET`).
4. Current expected/observed behavior: request passes through `Authenticate` (via `AuthenticateExternalInitiator`), `isUser` is `true` (synthetic user with `UserRoleRun`), `strconv.ParseInt` succeeds, and `prc.App.RunJobV2(ctx, J2.ID, nil)` is invoked, returning HTTP 200 with a pipeline run resource for `J2`.
5. Assert instead that the response should be `401`/`403` because `EI-A` has no authorization over `J2`. Failing this assertion (i.e., getting 200) confirms the vulnerability.

### Citations

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

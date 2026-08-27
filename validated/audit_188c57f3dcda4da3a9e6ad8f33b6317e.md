### Title
External Initiator authentication bypasses the "users only" integer-ID job-run restriction and lacks job binding checks - ([File: core/web/pipeline_runs_controller.go])

### Summary
`PipelineRunsController.Create` intends to allow only session/API-token *users* (not External Initiators) to trigger runs via integer job IDs, using `isUser` from `auth.GetAuthenticatedUser(c)` as the gate. However, `auth.AuthenticateExternalInitiator` also sets `SessionUserKey` to a synthetic `User{Role: UserRoleRun}`, so `GetAuthenticatedUser` returns `true` for EI-authenticated requests as well. Combined with `RequiresRunRole` only checking `user.Role != UserRoleView` (never checking which job/bridge the caller is bound to), any valid EI credential can trigger a run for an arbitrary job ID, not just the job it was registered for.

### Finding Description
The route `POST /v2/jobs/:ID/runs` is wrapped by `auth.RequiresRunRole(prc.Create)` and authenticated via a method chain that includes `auth.AuthenticateExternalInitiator`. That middleware, on success, does: [1](#0-0) 
which sets both `SessionExternalInitiatorKey` (the actual EI record) and `SessionUserKey` to a fabricated `User{Role: UserRoleRun}` — with **no reference to which job/bridge that EI is entitled to run**.

`RequiresRunRole` then only checks the role is not `View`: [2](#0-1) 
It never inspects `GetAuthenticatedExternalInitiator(c)` nor cross-references it against `c.Param("ID")`.

In `PipelineRunsController.Create`, the code comment claims "only users are allowed to run jobs using int IDs - EIs not allowed", and gates on `isUser`: [3](#0-2) 
But because `AuthenticateExternalInitiator` also populates `SessionUserKey`, `isUser` (i.e., `auth.GetAuthenticatedUser(c)` returning `ok=true`) is **true for EI-authenticated requests too**. The intended distinguishing check the comment describes does not actually exist in code — there is no check of `isUser`'s underlying identity type, `Role`, or any binding between the External Initiator and the job ID in the path. Once `idStr` parses as an int32, `prc.App.RunJobV2(ctx, jobID, nil)` is invoked unconditionally for that integer job ID, regardless of whether the authenticated principal is a real user or an EI, and regardless of which job/bridge that EI was registered against.

The only path that historically enforced EI-to-job binding was the UUID-based webhook trigger flow (matching `job.ExternalInitiatorWebhookSpecs`), and that entire code path has been intentionally removed ("cannot run job of type ... job type removed"): [4](#0-3) 
leaving the integer-ID branch as the only live path, with no equivalent binding validation ever added to it.

### Impact Explanation
An External Initiator credential holder bound only to job A can submit `POST /v2/jobs/{jobB_int_ID}/runs` and successfully trigger a pipeline run for job B, a job/bridge it was never authorized for. This breaks the REQUEST_BINDING invariant (one EI credential bound to one authorized job) and results in unauthorized job execution — matching the "unauthorized job run triggered by wrong EI" impact class. Depending on what job B does (e.g., writing on-chain, calling external bridges), this could cause unintended fund movement, spurious on-chain transactions, or unauthorized use of bridge credentials/quota.

### Likelihood Explanation
Preconditions are minimal: only a single valid, low-privilege EI `accessKey`/`secret` pair registered for any job is required (EI registration itself requires only an authenticated node user with `run` role or higher via `/v2/external_initiators`, which is a normal operational credential, not admin). No knowledge of job B's owner credentials is needed — only its integer job ID, which is often discoverable via job listing endpoints or predictable sequential IDs. The exploit is a single unauthenticated-context HTTP POST with EI headers, fully repeatable.

### Recommendation
In `PipelineRunsController.Create`, explicitly distinguish EI-authenticated requests from real user sessions (e.g., check `auth.GetAuthenticatedExternalInitiator(c)` presence, or use a dedicated context key/role rather than reusing `SessionUserKey` for EIs), and reject the integer-ID run path entirely for EI callers as the comment intends. If EI-triggered runs are still meant to be supported for any job type, restore binding validation that verifies the authenticated EI is actually associated with the target job's spec (e.g., via `ExternalInitiatorWebhookSpecs` or equivalent) before calling `RunJobV2`.

### Proof of Concept
Go handler-level integration test plan (`core/web/pipeline_runs_controller_test.go`):
1. Create Job A and Job B (both integer-ID, non-webhook-UUID-triggered job types, e.g. `webhook`/`directrequest` or any type acceptable to `RunJobV2`).
2. Register External Initiator `EI-A` via `cltest.CreateExternalInitiatorViaWeb`, unrelated to Job B.
3. Send `POST /v2/jobs/{JobB.ID}/runs` with headers `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` set to `EI-A`'s credentials (no user session/API token).
4. Assert expected behavior per design intent: `401`/`403` (EI rejected on integer-ID path) or a binding-check failure.
5. Verify actual observed behavior: request succeeds (`200`) and a `pipeline_runs` row is created for Job B, confirming `isUser` incorrectly evaluates `true` for the EI and no job binding is validated, i.e., `assert.Equal(t, http.StatusOK, resp.StatusCode)` and `cltest.AssertCountIncreased(t, app.GetDB(), "pipeline_runs", 0, 1)` for Job B's run.

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

**File:** core/web/pipeline_runs_controller.go (L101-127)
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

	jsonAPIError(c, http.StatusUnprocessableEntity, errors.New("bad job ID"))
```

### Title
External-initiator credentials bypass the "EIs not allowed" restriction and can trigger runs on any job by numeric ID - ([File: core/web/auth/auth.go], [File: core/web/pipeline_runs_controller.go])

### Finding Description
`auth.AuthenticateExternalInitiator` authenticates an external-initiator (EI) request by access key/secret and, on success, stores the EI object under `SessionExternalInitiatorKey` and *also* fabricates a generic session user with run role: `c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})` [1](#0-0) . This mirrors the exact same `SessionUserKey` that `AuthenticateBySession`/`AuthenticateByToken` set for real, credentialed users [2](#0-1) [3](#0-2) .

`PipelineRunsController.Create` (mounted at `POST /v2/jobs/:ID/runs` behind the combined `AuthenticateExternalInitiator | AuthenticateByToken | AuthenticateBySession` chain) explicitly intends to forbid EIs from triggering runs via numeric job IDs, per its own comment: "only users are allowed to run jobs using int IDs - EIs not allowed" [4](#0-3) . However, the gating check is `_, isUser := auth.GetAuthenticatedUser(c)` [5](#0-4) , and `GetAuthenticatedUser` simply reads whatever is stored at `SessionUserKey` [6](#0-5) . Since `AuthenticateExternalInitiator` populates that same key with a placeholder `User{Role: UserRoleRun}`, `isUser` evaluates to `true` for EI-authenticated requests exactly as it does for genuine session/token users. As a result the "EIs not allowed" branch is bypassed and `prc.App.RunJobV2(ctx, jobID, nil)` executes for any numeric job ID supplied by the EI holder — with no check that the job is associated with the calling EI at all.

Confirming this, `GetAuthenticatedExternalInitiator` (the function that extracts the actual EI object bound during authentication) is defined but never called anywhere else in the codebase, so no handler cross-checks the authenticated EI against the target job's configured external initiator. The webhook/EI-name-keyed job-run path was removed (per the `job.ErrJobTypeRemoved` check just above), but the numeric-ID path remains reachable by EI credentials due to this authentication-context confusion, not by design.

### Impact Explanation
An EI credential holder registered for one external initiator can trigger pipeline runs on **any** job addressed by numeric job ID, including jobs that were never configured to trust that EI. This is unauthorized job-run execution / cross-tenant impersonation — a job trigger control bypass matching Chainlink's "unauthorized job run" bounty impact class. Depending on the pipeline (e.g., jobs that move funds, call external actions, or produce reports the job owner did not intend to trigger), this can cause unintended state changes, unwanted external calls, or resource exhaustion via unauthorized runs.

### Likelihood Explanation
Exploitation requires only a valid EI access key/secret for *any single* EI (a low-privilege, non-admin credential type routinely provisioned to external services) and knowledge/guessing of a target job's numeric ID (job IDs are small sequential integers, easily enumerable). No operator, admin, or additional session is needed — a single unauthenticated-relative-to-target-job HTTP POST suffices, and the flaw is deterministic/repeatable for every request.

### Recommendation
Distinguish EI-derived pseudo-users from real users, e.g., use a distinct context key or a `IsExternalInitiator` flag instead of overloading `SessionUserKey`, so `GetAuthenticatedUser`/`isUser` cannot be satisfied by EI authentication. Alternatively, in `PipelineRunsController.Create`, explicitly check `auth.GetAuthenticatedExternalInitiator(c)` and reject the request (or verify the target job's `ExternalJobID`/EI binding) whenever an EI object is present in context, regardless of what `SessionUserKey` holds.

### Proof of Concept
1. Create two jobs, `jobA` (int32 ID) and `jobB` (int32 ID), owned by different logical consumers.
2. Register EI `EI1` via `cltest.CreateExternalInitiatorViaWeb` and obtain its `AccessKey`/`Secret`.
3. Issue `POST /v2/jobs/{jobB.ID}/runs` with headers `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` set to `EI1`'s credentials (no session/API token headers).
4. Assert the response is `200 OK` with a created pipeline run for `jobB`, and that `pipeline_runs` table has a new row for `jobB.ID` — demonstrating `EI1` triggered a run on a job with which it has no configured relationship, contrary to the "EIs not allowed" comment in `pipeline_runs_controller.go`.
5. As a control, add an assertion (currently failing) that `auth.GetAuthenticatedExternalInitiator(c)` is checked in `Create` and the request is rejected with `401/403` when the caller is EI-authenticated for numeric job IDs.

### Citations

**File:** core/web/auth/auth.go (L55-71)
```go
func AuthenticateBySession(c *gin.Context, authr Authenticator) error {
	ctx := c.Request.Context()
	session := sessions.Default(c)
	sessionID, ok := session.Get(SessionIDKey).(string)
	if !ok {
		return auth.ErrorAuthFailed
	}

	user, err := authr.AuthorizedUserWithSession(ctx, sessionID)
	if err != nil {
		return err
	}

	c.Set(SessionUserKey, &user)

	return nil
}
```

**File:** core/web/auth/auth.go (L78-112)
```go
func AuthenticateByToken(c *gin.Context, authr Authenticator) error {
	ctx := c.Request.Context()
	token := &auth.Token{
		AccessKey: c.GetHeader(APIKey),
		Secret:    c.GetHeader(APISecret),
	}
	if token.AccessKey == "" {
		return auth.ErrorAuthFailed
	}

	if token.Secret == "" {
		return auth.ErrorAuthFailed
	}

	// We need to first load the user row so we can compare tokens using the stored salt
	user, err := authr.FindUserByAPIToken(ctx, token.AccessKey)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) || errors.Is(err, clsessions.ErrUserSessionExpired) {
			return auth.ErrorAuthFailed
		}
		return err
	}

	ok, err := clsessions.AuthenticateUserByToken(token, &user)
	if err != nil {
		return err
	}
	if !ok {
		return auth.ErrorAuthFailed
	}

	c.Set(SessionUserKey, &user)

	return nil
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

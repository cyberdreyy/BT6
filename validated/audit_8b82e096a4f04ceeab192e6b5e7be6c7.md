### Title
EI-authenticated requests satisfy `isUser` check in `PipelineRunsController.Create`, bypassing "EIs not allowed" numeric-ID job-run restriction - ([File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` sets `c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})` for any valid EI access-key/secret pair, and `GetAuthenticatedUser` only checks whether `SessionUserKey` is present and type-asserts to `*clsessions.User` — it does not distinguish a real authenticated user session from this synthetic EI-run-role user. As a result, `isUser` in `PipelineRunsController.Create` evaluates to `true` for EI credentials, allowing the numeric-job-ID `RunJobV2` branch to execute even though the code comment explicitly states "EIs not allowed" for that path.

### Finding Description
`core/web/auth/auth.go` defines three `authMethod` implementations. `AuthenticateBySession` and `AuthenticateByToken` set `SessionUserKey` to a real `clsessions.User` fetched from the datastore [1](#0-0) , but `AuthenticateExternalInitiator` also sets `SessionUserKey`, using a freshly constructed synthetic user object with only `Role: clsessions.UserRoleRun` populated: [2](#0-1) 

`GetAuthenticatedUser` simply reads that key and returns `ok=true` if any `*clsessions.User` is present, with no way to tell it apart from a session/token-derived user: [3](#0-2) 

`PipelineRunsController.Create` relies on this boolean to gate the numeric-job-ID `RunJobV2` execution path, and its own comment states the intended restriction: "only users are allowed to run jobs using int IDs - EIs not allowed": [4](#0-3) 

Since `isUser` is derived purely from key presence/type, and `AuthenticateExternalInitiator` populates that same key with a `Role: UserRoleRun` user object, an EI-authenticated request satisfies `isUser == true` just like a real session/token user. The `uuid.Parse` short-circuit only blocks UUID-formatted IDs (the removed Webhook job type's external IDs); it does nothing for numeric IDs, so the numeric-ID branch is reached and `prc.App.RunJobV2(ctx, jobID, nil)` executes for any job ID supplied, regardless of whether that job belongs to the EI or any other job on the node.

The existence of the explicit "EIs not allowed" comment confirms the intended design was to block EI-authenticated numeric-ID job triggering — but the implementation check (`isUser` from `GetAuthenticatedUser`) does not actually enforce that distinction, because it cannot differentiate a real user session from the synthetic EI-issued `UserRoleRun` object.

### Impact Explanation
An external-initiator credential (access key + secret), which is meant to be scoped only to trigger its associated webhook job (a job type that has since been removed per `job.ErrJobTypeRemoved`), can instead be used to invoke `RunJobV2` against **any** numeric job ID on the node. This is an authorization-bypass / unauthorized-job-run vulnerability: a low-privilege EI credential holder gains the ability to trigger runs of arbitrary jobs configured on the node, which may result in unauthorized on-chain actions, unintended fund movement (e.g., triggering a keeper/upkeep-adjacent job), or unwanted resource consumption/spam of pipeline execution, none of which the EI credential was scoped to permit.

### Likelihood Explanation
The only precondition is possession of a valid (or leaked/guessable) EI access-key+secret pair — a credential class explicitly listed as in-scope for an unprivileged attacker. No admin/session/API-token credentials are needed. The request is a simple, repeatable `POST /v2/jobs/:ID/runs` with `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers and a numeric job ID; the attacker can iterate over numeric job IDs to enumerate and trigger arbitrary jobs. This is fully reproducible via a unit/handler test.

### Recommendation
Distinguish EI-derived authentication from real user authentication instead of relying solely on `SessionUserKey` presence. Options:
- Have `AuthenticateExternalInitiator` avoid setting `SessionUserKey` at all (or set it to `nil`/a sentinel), and instead make `RequiresRunRole` and any EI-permitted handlers check `GetAuthenticatedExternalInitiator` explicitly.
- In `PipelineRunsController.Create`, replace the `isUser` check with an explicit check that the authenticated entity is NOT an external initiator (`_, isEI := auth.GetAuthenticatedExternalInitiator(c); if isEI { reject }`) in addition to/instead of `GetAuthenticatedUser`.
- More robust: tag the context-stored object so `GetAuthenticatedUser` can differentiate real DB-backed users from synthetic role-only users (e.g., a separate marker field or type).

### Proof of Concept
Go handler-level test plan (in `core/web/pipeline_runs_controller_test.go` or a new test):
1. Construct a `gin.Context` with a request `POST /v2/jobs/123/runs`.
2. Instead of calling `AuthenticateBySession`/`AuthenticateByToken`, invoke `auth.AuthenticateExternalInitiator(c, mockAuthenticator)` where `mockAuthenticator.FindExternalInitiator` returns a valid `bridges.ExternalInitiator` and `bridges.AuthenticateExternalInitiator` succeeds (matching hashed secret).
3. Assert `user, ok := auth.GetAuthenticatedUser(c)` returns `ok == true` and `user.Role == clsessions.UserRoleRun`.
4. Call `PipelineRunsController.Create(c)` with `App.RunJobV2` mocked to expect being called with `jobID=123`.
5. Assert `RunJobV2` was invoked (i.e., the numeric-ID branch executed) and the response is a successful pipeline-run resource — proving that EI-only credentials bypass the "EIs not allowed" comment/intended restriction.

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

**File:** core/web/auth/auth.go (L143-151)
```go
	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})

	return nil
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

**File:** core/web/pipeline_runs_controller.go (L101-125)
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

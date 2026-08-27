This confirms the vulnerability is real: the route is registered at `/v2/jobs/:ID/runs` with middleware chain `auth.AuthenticateExternalInitiator, auth.AuthenticateByToken, auth.AuthenticateBySession` wrapped in `auth.RequiresRunRole(prc.Create)` [1](#0-0) . `AuthenticateExternalInitiator` sets `c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})` after successfully validating EI credentials [2](#0-1) . Since `GetAuthenticatedUser` simply reads the `SessionUserKey` context value regardless of how it got set [3](#0-2) , `isUser` in `Create` evaluates to `true` for an EI-authenticated request, directly contradicting the code comment "only users are allowed to run jobs using int IDs - EIs not allowed" [4](#0-3) .

### Title
EI credentials bypass "EIs not allowed" check in PipelineRunsController.Create, letting external initiators trigger arbitrary numeric job IDs - (File: core/web/pipeline_runs_controller.go)

### Summary
`PipelineRunsController.Create` intends to restrict numeric job-ID (int32) run triggering to human users only, per its comment, using `auth.GetAuthenticatedUser(c)` to detect "isUser". However, `AuthenticateExternalInitiator` populates the same `SessionUserKey` context value with a synthetic `User{Role: UserRoleRun}` for any successfully authenticated EI, making `isUser` true and the "EIs not allowed" check ineffective.

### Finding Description
The route `/v2/jobs/:ID/runs` (POST) is registered with middleware `auth.Authenticate(..., auth.AuthenticateExternalInitiator, auth.AuthenticateByToken, auth.AuthenticateBySession)` and wrapped by `auth.RequiresRunRole(prc.Create)` [1](#0-0) . When an attacker authenticates solely with valid EI headers (`OCR-EXTERNAL-INITIATOR-ACCESS-KEY`/`OCR-EXTERNAL-INITIATOR-SECRET` equivalents), `AuthenticateExternalInitiator` succeeds and sets both `SessionExternalInitiatorKey` and `SessionUserKey` to a `&clsessions.User{Role: clsessions.UserRoleRun}` [5](#0-4) . `RequiresRunRole` then passes because the synthetic user's role is `UserRoleRun` (>= run) [6](#0-5) . Inside `Create`, `_, isUser := auth.GetAuthenticatedUser(c)` reads the exact same `SessionUserKey` that the EI auth path set, so `isUser` evaluates `true` [7](#0-6) . The code then proceeds to parse `idStr` as an int32 job ID and calls `prc.App.RunJobV2(ctx, jobID, nil)` for *any* numeric job ID, with no check that the job is bound to this specific EI or is even a webhook-type job tied to that initiator [8](#0-7) . This defeats the intended design (visible from the comment itself) that EIs should only be able to trigger jobs they own/are bound to (via UUID-based webhook binding, now removed) — not any arbitrary numeric job ID belonging to any job type or owner.

### Impact Explanation
An external-initiator credential holder — a relatively low-trust credential class meant only to post job run requests for its own bound webhook job — can trigger `RunJobV2` for **any** job in the node identified by numeric ID, regardless of job type or ownership. This is an authorization-exactness violation / request-binding bypass: EI credentials are elevated to behave like a full "run" role user for all jobs, not just their bound job. Depending on job configurations, this could result in unauthorized triggering of on-chain transactions, unauthorized use of node resources, or interference with jobs unrelated to the EI's owner. This maps to Chainlink's "unauthorized job run" bounty impact class.

### Likelihood Explanation
Only valid EI credentials are required (no user session, no API token, no edit/admin role) — the minimal precondition explicitly allowed by the threat model. The request is a single `POST /v2/jobs/<numeric-id>/runs` with EI headers; it is deterministic and repeatable. Any legitimate EI (even one meant for a completely different bridge/job) can iterate numeric job IDs to trigger unrelated jobs.

### Recommendation
In `PipelineRunsController.Create`, do not rely on `auth.GetAuthenticatedUser` alone to distinguish real users from EIs, since `AuthenticateExternalInitiator` synthesizes a `SessionUserKey` entry. Instead, explicitly check `auth.GetAuthenticatedExternalInitiator(c)` and reject (or separately validate binding) when an EI identity is present, e.g.:
```go
if _, isEI := auth.GetAuthenticatedExternalInitiator(c); isEI {
    jsonAPIError(c, http.StatusUnprocessableEntity, errors.New("bad job ID"))
    return
}
```
placed before or in place of the `isUser` check, so that only genuine session/token users (without an EI identity) can hit the int32 job-ID path. Alternatively, stop setting `SessionUserKey` in `AuthenticateExternalInitiator` and instead have `RequiresRunRole` recognize EI identity independently via `SessionExternalInitiatorKey`.

### Proof of Concept
Go handler-level integration test plan (extending `core/web/pipeline_runs_controller_test.go`):
1. Set up a test app with an `ExternalInitiator` record and a separate numeric job (e.g., a cron or keeper job) not bound to that EI, mocking `App.RunJobV2` to fail the test if invoked.
2. Send `POST /v2/jobs/<numericJobID>/runs` with `OCR-EXTERNAL-INITIATOR-ACCESS-KEY`/`SECRET` headers set to the valid EI credentials, and no session cookie or API token headers.
3. Assert: `RunJobV2` mock is called (demonstrating current vulnerable behavior) — before fix, expect HTTP 200 and a `RunJobV2` invocation; after applying the recommended fix, assert HTTP 422 ("bad job ID") and that `RunJobV2` is never called, matching the code comment's intended behavior.

### Citations

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

**File:** core/web/auth/auth.go (L119-150)
```go
func AuthenticateExternalInitiator(c *gin.Context, store Authenticator) error {
	ctx := c.Request.Context()
	eia := &auth.Token{
		AccessKey: c.GetHeader(static.ExternalInitiatorAccessKeyHeader),
		Secret:    c.GetHeader(static.ExternalInitiatorSecretHeader),
	}

	ei, err := store.FindExternalInitiator(ctx, eia)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return auth.ErrorAuthFailed
		}

		return errors.Wrap(err, "finding external initiator")
	}

	ok, err := bridges.AuthenticateExternalInitiator(eia, ei)
	if err != nil {
		return err
	}
	if !ok {
		return auth.ErrorAuthFailed
	}

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

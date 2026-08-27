## Analysis

The route `POST /v2/jobs/:ID/runs` is registered in the `userOrEI` group with `auth.Authenticate(..., auth.AuthenticateExternalInitiator, auth.AuthenticateByToken, auth.AuthenticateBySession)` wrapped by `auth.RequiresRunRole(prc.Create)`. [1](#0-0) 

`AuthenticateExternalInitiator` looks up the `ExternalInitiator` solely by `AccessKey`/`Secret` via `FindExternalInitiator`, and on success sets **both** `SessionExternalInitiatorKey` and a synthetic `SessionUserKey` with `Role: UserRoleRun` — it does not bind the authenticated identity to any specific job ID. [2](#0-1) 

`RequiresRunRole` only checks that `user.Role != UserRoleView`; it has no concept of job ownership, so an EI credential (Role=Run) passes. [3](#0-2) 

In `PipelineRunsController.Create`, the only check performed against `:ID` is `isUser, _ := auth.GetAuthenticatedUser(c)`, followed by `if isUser { ...ParseInt(idStr)... RunJobV2(ctx, jobID, nil) }`. Because `AuthenticateExternalInitiator` also populates `SessionUserKey`, `GetAuthenticatedUser(c)` returns `ok == true` for EI-authenticated requests as well — the comment "only users are allowed to run jobs using int IDs - EIs not allowed" is not actually enforced by this check, since the code cannot distinguish a real session/token user from an EI-authenticated pseudo-user. [4](#0-3) 

However, critically, `RunJobV2` is invoked with the integer `jobID` parsed straight from the URL parameter — there is **no lookup, filter, or comparison** anywhere in `Create` that ties the authenticated `ExternalInitiator` (`ei.ID`/`ei.Name`) to the job being requested. The EI's binding to a specific job only exists in the job spec's `externalInitiators` TOML list (used at webhook trigger time for old-style webhook UUID jobs), but that binding is not consulted in this integer-ID code path at all.

That said, for this to be practically exploitable the target job (job Y) must be a job type reachable through `RunJobV2` with `nil` request body (e.g., a job type that doesn't require variable input, such as certain non-webhook jobs), and the attacker must guess/know job Y's integer ID — which is not secret (auto-incrementing DB ID, discoverable in many contexts) but does add friction. Webhook jobs specifically reject the integer-ID path early via UUID rejection: `if _, err := uuid.Parse(idStr); err == nil { ... ErrJobTypeRemoved }`, and non-webhook trigger via EI headers for integer job IDs is not restricted to jobs that declared that EI in `externalInitiators`.

This is a legitimate authorization-binding gap: an EI credential scoped conceptually to "trigger run for job X" can, via the RBAC middleware layering that grants it a generic `Role: Run` session, invoke `POST /v2/jobs/:ID/runs` for **any** integer job ID Y on the node, not just the job it was registered against.

### Title
External-initiator credential can trigger runs for arbitrary job IDs, not just its registered job - ([File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` grants any valid external-initiator credential a generic `Role: Run` pseudo-user session without binding it to the specific job it was created for, and `PipelineRunsController.Create` performs no ownership check between the authenticated EI and the `:ID` path parameter before calling `RunJobV2`. As a result, an EI credential valid for job X can trigger `RunJobV2` for any other integer job ID Y on the node.

### Finding Description
The route `POST /v2/jobs/:ID/runs` is registered under `userOrEI` with `auth.Authenticate(..., AuthenticateExternalInitiator, AuthenticateByToken, AuthenticateBySession)` and `auth.RequiresRunRole(prc.Create)` [1](#0-0) . `AuthenticateExternalInitiator` authenticates solely against `access_key`/`secret` headers via `FindExternalInitiator(ctx, eia)`, which looks the initiator up only by `AccessKey` — with no relation to a job ID [5](#0-4) . On success, it sets `c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})` [6](#0-5) , which satisfies `RequiresRunRole`'s only check (`user.Role != UserRoleView`) [3](#0-2) .

Inside `PipelineRunsController.Create`, the code checks `isUser, _ := auth.GetAuthenticatedUser(c)` and, if true, parses `:ID` as an int32 and calls `prc.App.RunJobV2(ctx, jobID, nil)` directly — with no lookup of `GetAuthenticatedExternalInitiator(c)` and no comparison against the job's configured external initiators [4](#0-3) . Because the EI auth path also populates `SessionUserKey`, `isUser` is true for EI requests, contradicting the code's own comment that "EIs [are] not allowed" on the integer-ID path. No code anywhere in `Create` reads `ei.Name`/`ei.ID` to restrict which job IDs the authenticated EI may target.

### Impact Explanation
An attacker holding a legitimately-issued EI credential for job X can invoke `RunJobV2` for arbitrary job Y, causing unauthorized job execution on jobs it was never registered against. Depending on job Y's task graph, this can cause unintended external HTTP calls, fund-moving actions, or resource exhaustion via unauthorized pipeline runs — this maps to Chainlink's "unauthorized job run" / authorization bypass impact class.

### Likelihood Explanation
Requires only a valid, low-privilege EI `access_key`/`secret` pair for any job (a credential type intentionally scoped to a single job's webhook trigger) and knowledge/guessing of another job's integer ID. No admin/operator access is needed, and the request is a single unauthenticated (relative to session/token) HTTP POST, making it trivially repeatable once an EI credential and target job ID are known.

### Recommendation
In `PipelineRunsController.Create`, when the authenticated principal is an external initiator (`auth.GetAuthenticatedExternalInitiator(c)`), verify that the target job (looked up by `jobID`) actually lists that EI in its `externalInitiators`/webhook spec before invoking `RunJobV2`; reject with `403 Forbidden` otherwise. Alternatively, do not set a generic `SessionUserKey` with `Role: Run` for EI auth — instead thread the `ExternalInitiator` identity separately so `Create` can explicitly branch and enforce per-job binding rather than relying on `isUser` as a proxy.

### Proof of Concept
Go handler-integration test (extending `core/web/pipeline_runs_controller_test.go` style):
1. Create two jobs, `jobX` (non-webhook, runnable via `RunJobV2` with nil vars) and `jobY`, via `cltest.CreateJobViaWeb`.
2. Create an `ExternalInitiator` record explicitly tied conceptually to `jobX` via `cltest.CreateExternalInitiatorViaWeb`, obtaining `eia := &auth.Token{AccessKey, Secret}`.
3. Send `POST /v2/jobs/<jobY.ID>/runs` using `cltest.UnauthenticatedPost` with headers `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` set to the EI's credentials for jobX.
4. Assert: current behavior returns `200 OK` with a `pipelineRun` resource created for `jobY` (violating binding) — expected/fixed behavior should return `403 Forbidden` and `AssertCountStays(t, db, "pipeline_runs", 0)` for jobY.

### Citations

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

**File:** core/web/auth/auth.go (L119-151)
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

**File:** core/web/pipeline_runs_controller.go (L101-128)
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
}
```

**File:** core/bridges/orm.go (L262-267)
```go
// FindExternalInitiator finds an external initiator given an authentication request
func (o *orm) FindExternalInitiator(ctx context.Context, eia *auth.Token) (*ExternalInitiator, error) {
	exi := &ExternalInitiator{}
	err := o.ds.GetContext(ctx, exi, `SELECT * FROM external_initiators WHERE access_key = $1`, eia.AccessKey)
	return exi, err
}
```

### Title
External Initiator Run-role session bypasses per-job/bridge binding, enabling arbitrary job-run triggering - ([File: core/web/auth/auth.go -> AuthenticateExternalInitiator])

### Finding Description
`AuthenticateExternalInitiator` unconditionally stores the matched `bridges.ExternalInitiator` in `SessionExternalInitiatorKey` and *also* injects a synthetic `&clsessions.User{Role: clsessions.UserRoleRun}` into `SessionUserKey`: [1](#0-0) 

The only route gate that consumes this session is `RequiresRunRole`, which reads solely `c.Get(SessionUserKey)` and checks the role field — it never inspects `SessionExternalInitiatorKey` to confirm the caller's EI credential is bound to the specific job/bridge being acted upon: [2](#0-1) 

The router wires `AuthenticateExternalInitiator` into the shared `userOrEI` group, which exposes `POST /v2/jobs/:ID/runs` (`RequiresRunRole(prc.Create)`) — a route parameterized by an arbitrary job `:ID`, not scoped to the initiator's own bridge: [3](#0-2) 

Critically, `GetAuthenticatedExternalInitiator` — the accessor that would let a handler validate that the authenticated EI actually corresponds to the target job's external-initiator/bridge binding — is defined but never called anywhere else in the codebase: [4](#0-3) 

Since `PipelineRunsController.Create` (bound to `POST /v2/jobs/:ID/runs`) is reached purely through the generic `RequiresRunRole` check on `SessionUserKey`, and no code path cross-references `SessionExternalInitiatorKey` against the requested `:ID`'s job/bridge, any valid EI credential is functionally equivalent to a full Run-role API token/session for the purpose of triggering runs on *any* job ID, not just the job wired to that EI's bridge.

### Impact Explanation
An external-initiator credential — intended to be a narrowly-scoped bridge-trigger credential — can be used to trigger pipeline runs for jobs unrelated to its own registered bridge, by simply varying `:ID` in `POST /v2/jobs/:ID/runs`. This is an authorization-scope violation: a low-privileged, single-purpose credential gains the ability to trigger arbitrary job executions node-wide, which can have downstream effects (unwanted on-chain transactions, resource exhaustion, unauthorized pipeline execution) depending on what other jobs do on this node. This corresponds to the "unauthorized job run" impact class.

### Likelihood Explanation
Preconditions are minimal: possession of any single valid `ExternalInitiator` AccessKey/Secret pair (issued to any bridge integration) is sufficient. No additional privilege, secret disclosure, or race condition is required — the attacker simply sends `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers and calls `POST /v2/jobs/<any-ID>/runs`. This is fully reproducible and repeatable.

### Recommendation
In `RequiresRunRole` (or specifically in the `/jobs/:ID/runs` handler path), when the session was established via `AuthenticateExternalInitiator`, use `auth.GetAuthenticatedExternalInitiator(c)` to verify that the target job `:ID` is actually driven by that specific external initiator/bridge before allowing the run to be created. Alternatively, split the route/middleware so EI-authenticated requests go through a distinct handler that enforces the job-to-EI binding, rather than sharing the generic `RequiresRunRole` gate with full API-token/session Run-role users.

### Proof of Concept
1. Register two jobs, Job A wired to webhook/bridge tied to `ExternalInitiator` EI-1, and Job B wired to a different, unrelated bridge/initiator.
2. In a handler-level Gin test, construct a request to `POST /v2/jobs/{JobB.ID}/runs` with headers `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` set to EI-1's credentials, routed through `auth.Authenticate(..., auth.AuthenticateExternalInitiator, ...)` then `auth.RequiresRunRole(prc.Create)`.
3. Assert the request succeeds (run created for Job B) despite EI-1 having no relationship to Job B — demonstrating that `RequiresRunRole` and `PipelineRunsController.Create` never call `GetAuthenticatedExternalInitiator` to scope the action.
4. Expected (fixed) behavior: request should be rejected with 401/403 because EI-1 is not bound to Job B.

### Citations

**File:** core/web/auth/auth.go (L143-148)
```go
	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})
```

**File:** core/web/auth/auth.go (L189-198)
```go
// GetAuthenticatedExternalInitiator extracts the external initiator from the
// context.
func GetAuthenticatedExternalInitiator(c *gin.Context) (*bridges.ExternalInitiator, bool) {
	obj, ok := c.Get(SessionExternalInitiatorKey)
	if !ok {
		return nil, false
	}

	return obj.(*bridges.ExternalInitiator), ok
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

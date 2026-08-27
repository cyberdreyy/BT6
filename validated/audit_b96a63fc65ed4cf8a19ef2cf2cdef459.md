### Title
Missing role gate on GET /v2/external_initiators discloses AccessKey/OutgoingToken to any authenticated (view/run-role) user - ([File: core/web/router.go])

### Finding Description
The `authv2` route group wires `GET /external_initiators` to `paginatedRequest(eic.Index)` with no role wrapper, while the mutating routes on the same resource, `POST /external_initiators` and `DELETE /external_initiators/:Name`, are wrapped in `auth.RequiresEditRole`. `ExternalInitiatorsController.Index` fetches all external initiators from `eic.App.BridgeORM().ExternalInitiators(ctx, offset, size)` and maps each row through `presenters.NewExternalInitiatorResource(initiator)` without redacting the `AccessKey`/`OutgoingToken` fields, then returns them via `paginatedResponse` [1](#0-0) . Because the route only passes through the generic `Authenticate` session/token middleware (see `RequiresRunRole`/`RequiresEditRole` gating logic in `core/web/auth/auth.go`) and not `RequiresEditRole`, any authenticated user regardless of role (`view`, `run`, or `admin`) can call this endpoint and receive the full paginated list of all external initiators' `Name`, `URL`, `AccessKey`, and `OutgoingToken` system-wide [2](#0-1) . This is an authorization inconsistency: the two mutating endpoints on the same resource enforce edit-role, but the read endpoint that discloses credential material enforces no role check at all.

### Impact Explanation
This maps to Chainlink's "sensitive information disclosure" / "authorization bypass" bounty class: a minimally privileged (`view` or `run` role) authenticated user can enumerate every external initiator's `AccessKey` and `OutgoingToken` across the node, which are credential material used by external initiators to authenticate outbound job-run trigger requests. Leaking `AccessKey` (the public half of the EI credential pair) and `OutgoingToken` (used by the node to authenticate itself to the EI callback) narrows the remaining unknown to the `Secret`, materially reducing the effort needed for a follow-on credential-compromise/brute-force attack against the EI integration, and it violates the intended "secrets never leave, authorization exact per role" invariant.

### Likelihood Explanation
Precondition is only a valid low-privilege authenticated session or API token (`view` or `run` role) — no admin/edit privileges are required, since the `GET /external_initiators` route lacks any `RequiresEditRole`/`RequiresRunRole` wrapper unlike the sibling `POST`/`DELETE` routes. The request is a single unauthenticated-from-role-perspective HTTP `GET /v2/external_initiators?size=N` call, trivially repeatable and requires no special conditions beyond having any account on the node.

### Recommendation
Wrap the `GET /external_initiators` route with the same (or stricter) role gate used by the mutating routes, e.g. `auth.RequiresEditRole(paginatedRequest(eic.Index))` (or at minimum `RequiresAdminRole` given the credential sensitivity), and additionally redact `AccessKey`/`OutgoingToken` from `presenters.ExternalInitiatorResource` for list/index responses so that even edit-role users only see them once, at creation time (as already done via `presenters.NewExternalInitiatorAuthentication` in `Create`).

### Proof of Concept
1. In a `core/web` handler-level integration test (mirroring the pattern in `external_initiators_controller_test.go`), create a session/API token for a user with `sessions.UserRoleView`.
2. Seed one or more external initiators via `BridgeORM().CreateExternalInitiator`.
3. As the view-role user, issue `GET /v2/external_initiators?size=50` against the test app router.
4. Assert current (vulnerable) behavior: response status `200` and JSON body contains non-empty `incomingAccessKey`/`outgoingToken` fields for each initiator — demonstrating the missing role gate and secret disclosure.
5. Expected/fixed behavior: response should be `401`/`403` for the view-role user (matching `RequiresEditRole` behavior on `POST`/`DELETE`), or if `view` role is intended to have read access, the response body must have `AccessKey`/`OutgoingToken` omitted/redacted.

### Citations

**File:** core/web/external_initiators_controller.go (L50-59)
```go
func (eic *ExternalInitiatorsController) Index(c *gin.Context, size, page, offset int) {
	ctx := c.Request.Context()
	externalInitiators, count, err := eic.App.BridgeORM().ExternalInitiators(ctx, offset, size)
	resources := make([]presenters.ExternalInitiatorResource, 0, len(externalInitiators))
	for _, initiator := range externalInitiators {
		resources = append(resources, presenters.NewExternalInitiatorResource(initiator))
	}

	paginatedResponse(c, "externalInitiators", size, page, resources, count, err)
}
```

**File:** core/web/auth/auth.go (L200-236)
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

// RequiresEditRole extracts the user object from the context, and asserts the user's role is at least
// 'edit'
func RequiresEditRole(handler func(*gin.Context)) func(*gin.Context) {
	return func(c *gin.Context) {
		user, ok := GetAuthenticatedUser(c)
		if !ok {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("not a valid session"))
			return
		}
		if user.Role == clsessions.UserRoleView || user.Role == clsessions.UserRoleRun {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("Unauthorized"))
			return
		}
		handler(c)
	}
}
```

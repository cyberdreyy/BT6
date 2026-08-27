I now have enough information to answer.

### Title
View-role user can list all external initiators including AccessKey and OutgoingToken via GET /v2/external_initiators - ([File: core/web/router.go])

### Summary
The `GET /v2/external_initiators` route is registered without any role wrapper (`auth.RequiresEditRole`/`RequiresRunRole`/`RequiresAdminRole`), unlike the `POST`/`DELETE` routes on the same resource which require Edit role. This is confirmed as intended by the test suite's RBAC matrix, which explicitly marks `GET /v2/external_initiators` as `viewOnlyAllowed: true`. The `Index` handler returns `AccessKey` and `OutgoingToken` fields for every external initiator in the response.

### Finding Description
In `core/web/router.go`, the route is wired as:
```go
authv2.GET("/external_initiators", paginatedRequest(eia.Index))
```
with no `auth.Requires*Role` wrapper, while the sibling `POST`/`DELETE` routes explicitly require `auth.RequiresEditRole`. [1](#0-0) 

`ExternalInitiatorsController.Index` (`core/web/external_initiators_controller.go`) queries all external initiators from the bridge ORM and maps each into a `presenters.ExternalInitiatorResource`, then returns them in a paginated JSON:API response. [2](#0-1) 

`presenters.ExternalInitiatorResource` includes `AccessKey` (json tag `accessKey`) and `OutgoingToken` (json tag `outgoingToken`) with no redaction: [3](#0-2) 

Since the route is only wrapped in `auth.Authenticate(...)` (session or token auth) with no role check, any authenticated user — including one with the lowest privilege `UserRoleView` — can call `GET /v2/external_initiators` and receive `AccessKey`/`OutgoingToken` for every registered external initiator. This is corroborated by the RBAC route test table, which explicitly documents `GET /v2/external_initiators` as `viewOnlyAllowed: true`: [4](#0-3)  and by the existing controller test `TestExternalInitiatorsController_Index`, which asserts `AccessKey`/`OutgoingToken` are returned in the response body without checking or restricting caller role: [5](#0-4) 

This violates the intended invariant that credential-bearing resources require exact/least-privilege authorization — the `POST` (create, which itself returns credentials once) and `DELETE` operations on this same resource correctly require Edit role, but `Index` (list), which discloses the same credential fields (`AccessKey`, `OutgoingToken`) for all initiators repeatedly, has no such restriction. `OutgoingToken`/`OutgoingSecret` fields on `ExternalInitiator` are used to authenticate outbound webhook calls to third parties (`OutgoingToken` is included in headers sent to the external initiator's URL), and `AccessKey` (with the associated Secret, not returned here, but AccessKey is still a lookup credential/identifier for that initiator's session/auth) — so this discloses sensitive access tokens.

### Impact Explanation
Any authenticated user, even the least-privileged View role, can enumerate all external initiators and obtain their `AccessKey` and `OutgoingToken` values, which are meant to be limited to Edit/Admin-level operators. This corresponds to a "sensitive credential/secret disclosure" bounty class — an unprivileged (View-role) principal gains access to authentication material (`AccessKey`, `OutgoingToken`) for external initiator integrations, which can potentially be used to impersonate the external initiator's outgoing calls or better understand/target the incoming webhook credentials, exceeding what a View role should be permitted to read.

### Likelihood Explanation
This requires only a valid View-role (or Run-role) session/API token — the lowest privilege tier available in the system — with no additional preconditions. It is directly reachable via a single unauthenticated-role-check `GET` request once logged in, is fully repeatable (paginated to dump the entire external initiators table), and is already effectively demonstrated by the existing test `TestExternalInitiatorsController_Index`, which never asserts on caller role and shows `AccessKey`/`OutgoingToken` present in the response for a default/no-role-restricted client.

### Recommendation
Wrap the `GET /v2/external_initiators` route with `auth.RequiresEditRole` (matching the `POST`/`DELETE` handlers on the same resource) in `core/web/router.go`, i.e. change:
```go
authv2.GET("/external_initiators", paginatedRequest(eia.Index))
```
to:
```go
authv2.GET("/external_initiators", auth.RequiresEditRole(paginatedRequest(eia.Index)))
```
Additionally, consider redacting `AccessKey`/`OutgoingToken` from `ExternalInitiatorResource` list output entirely, exposing only non-secret metadata (name, URL, timestamps) for listing purposes, similar to how other secret-bearing list endpoints avoid echoing credentials back.

### Proof of Concept
Go handler-level integration test plan (extending `core/web/external_initiators_controller_test.go`):
1. Start `cltest.NewApplicationWithConfig` with `ExternalInitiatorsEnabled = true`.
2. Insert two external initiators via `cltest.MustInsertExternalInitiatorWithOpts`.
3. Create a user session/API token with `sessions.UserRoleView` (see helper patterns used for role-based tests, e.g. as in `core/web/auth/auth_test.go`'s `routeRules`/`viewOnlyAllowed` table-driven test).
4. Using an HTTP client authenticated as this View-role user, call `client.Get("/v2/external_initiators?size=10")`.
5. Assert `http.StatusOK` is returned (not `403 Forbidden`/`401 Unauthorized`).
6. Parse the paginated response into `[]presenters.ExternalInitiatorResource` and assert `AccessKey` and `OutgoingToken` fields are non-empty for each returned initiator.
7. Compare against `POST`/`DELETE /v2/external_initiators` calls made by the same View-role client, which should return `403 Forbidden` (per `auth.RequiresEditRole`), demonstrating the inconsistency: an operation that discloses the very same credential fields (list) is unprotected while operations that only manipulate them are protected.

### Citations

**File:** core/web/router.go (L263-266)
```go
		eia := ExternalInitiatorsController{app}
		authv2.GET("/external_initiators", paginatedRequest(eia.Index))
		authv2.POST("/external_initiators", auth.RequiresEditRole(eia.Create))
		authv2.DELETE("/external_initiators/:Name", auth.RequiresEditRole(eia.Destroy))
```

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

**File:** core/web/presenters/external_initiators.go (L57-77)
```go
type ExternalInitiatorResource struct {
	JAID
	Name          string         `json:"name"`
	URL           *models.WebURL `json:"url"`
	AccessKey     string         `json:"accessKey"`
	OutgoingToken string         `json:"outgoingToken"`
	CreatedAt     time.Time      `json:"createdAt"`
	UpdatedAt     time.Time      `json:"updatedAt"`
}

func NewExternalInitiatorResource(ei bridges.ExternalInitiator) ExternalInitiatorResource {
	return ExternalInitiatorResource{
		JAID:          NewJAID(strconv.FormatInt(ei.ID, 10)),
		Name:          ei.Name,
		URL:           ei.URL,
		AccessKey:     ei.AccessKey,
		OutgoingToken: ei.OutgoingToken,
		CreatedAt:     ei.CreatedAt,
		UpdatedAt:     ei.UpdatedAt,
	}
}
```

**File:** core/web/auth/auth_test.go (L224-226)
```go
	{"GET", "/v2/external_initiators", true, true, true},
	{"POST", "/v2/external_initiators", false, false, true},
	{"DELETE", "/v2/external_initiators/MOCK", false, false, true},
```

**File:** core/web/external_initiators_controller_test.go (L104-109)
```go
	assert.Len(t, externalInitiators, 1)
	assert.Equal(t, strconv.FormatInt(eiBar.ID, 10), externalInitiators[0].ID)
	assert.Equal(t, eiBar.Name, externalInitiators[0].Name)
	assert.Nil(t, externalInitiators[0].URL)
	assert.Equal(t, eiBar.AccessKey, externalInitiators[0].AccessKey)
	assert.Equal(t, eiBar.OutgoingToken, externalInitiators[0].OutgoingToken)
```

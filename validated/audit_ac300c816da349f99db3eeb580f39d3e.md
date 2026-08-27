### Title
Unprivileged View-role users can read all external initiator secrets via unrestricted GET /v2/external_initiators - ([File: core/web/router.go])

### Summary
The `GET /v2/external_initiators` route is registered without any role gate, while its sibling `POST`/`DELETE` routes for the same resource require the `edit` role. As a result, any authenticated user (including the minimal `view` role) or a `view`-scoped API token can call `ExternalInitiatorsController.Index` and receive every external initiator's `AccessKey` and `OutgoingToken`.

### Finding Description
In `core/web/router.go`, the external initiator routes are: [1](#0-0) 
Note that `authv2.GET("/external_initiators", paginatedRequest(eia.Index))` has no `auth.RequiresEditRole`/`auth.RequiresAdminRole` wrapper — unlike `POST` and `DELETE` on the same resource, and unlike essentially every other sensitive `GET` in this block guarded via role checks. The `authv2` group only requires successful authentication via session or token (`auth.Authenticate(... AuthenticateByToken, AuthenticateBySession)`) — this succeeds for any valid session/token regardless of role, including `clsessions.UserRoleView`.

`ExternalInitiatorsController.Index` unconditionally loads all external initiators from the ORM and returns them via the presenter that includes the raw secret fields: [2](#0-1) 

The presenter does not redact anything: [3](#0-2) 

The existing test even asserts that `AccessKey` and `OutgoingToken` are returned in the response body: [4](#0-3) 

Compare to `RequiresRunRole`/`RequiresEditRole`/`RequiresAdminRole` definitions, which show `UserRoleView` is the lowest privilege and is explicitly rejected by all three role-gate helpers: [5](#0-4) 

Since `Index` is not wrapped by any of these, a `view`-role user (the lowest privilege authenticated role in the system) passes the `Authenticate` middleware and reaches the handler directly, with no role check ever executed.

### Impact Explanation
This is credential/secret disclosure: `AccessKey` and `OutgoingToken` are the credentials external initiators use to authenticate to the node (`AuthenticateExternalInitiator` in `core/web/auth/auth.go`, matched against `bridges.AuthenticateExternalInitiator`). Leaking these to a `view`-role user allows that low-privileged user to impersonate any external initiator and trigger job runs (`POST /v2/jobs/:ID/runs` is reachable by `AuthenticateExternalInitiator` and is auto-granted `UserRoleRun`, per `auth.go:145-148`), which can lead to unauthorized job execution and downstream fund movement/oracle actions depending on job configuration. This corresponds to a "sensitive data/secret exposure leading to unauthorized action" bounty class.

### Likelihood Explanation
Minimal precondition: possession of *any* valid, authenticated session or API token, even one provisioned with the lowest `view` role (e.g., a read-only auditor account or a restricted API key). No admin/edit privileges are needed. The request is a single unauthenticated-role GET (`GET /v2/external_initiators?size=N`) and is fully reproducible/repeatable since pagination allows enumerating all initiators in the system.

### Recommendation
Wrap the route with a role check consistent with the sensitivity of the data, e.g. `authv2.GET("/external_initiators", auth.RequiresEditRole(paginatedRequest(eia.Index)))` (matching the `Create`/`Destroy` routes), and/or strip `AccessKey`/`OutgoingToken` from the list-view presenter (only return them once, at creation time, as is already done for `ExternalInitiatorAuthentication`).

### Proof of Concept
1. In `core/web/external_initiators_controller_test.go`-style test, create an app with a user session/API token whose `Role == clsessions.UserRoleView` (see `cltest` helpers for creating sessions with a specific role, or `clsessions.User{Role: clsessions.UserRoleView}`).
2. Insert an external initiator via `cltest.MustInsertExternalInitiatorWithOpts`.
3. Issue `client.Get("/v2/external_initiators")` authenticated as the `view`-role user.
4. Assert `http.StatusOK` (not `401`/`403`), and that the parsed `presenters.ExternalInitiatorResource` list contains non-empty `AccessKey` and `OutgoingToken` fields matching the inserted initiator.
5. Contrast with `client.Post("/v2/external_initiators", ...)` and `client.Delete(...)` as the same `view`-role user, expecting `401 Unauthorized`, confirming the asymmetry: only `Index` lacks the role gate.

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

**File:** core/web/external_initiators_controller_test.go (L104-126)
```go
	assert.Len(t, externalInitiators, 1)
	assert.Equal(t, strconv.FormatInt(eiBar.ID, 10), externalInitiators[0].ID)
	assert.Equal(t, eiBar.Name, externalInitiators[0].Name)
	assert.Nil(t, externalInitiators[0].URL)
	assert.Equal(t, eiBar.AccessKey, externalInitiators[0].AccessKey)
	assert.Equal(t, eiBar.OutgoingToken, externalInitiators[0].OutgoingToken)

	resp, cleanup = client.Get(links["next"].Href)
	t.Cleanup(cleanup)
	cltest.AssertServerResponse(t, resp, http.StatusOK)

	externalInitiators = []presenters.ExternalInitiatorResource{}
	err = web.ParsePaginatedResponse(cltest.ParseResponseBody(t, resp), &externalInitiators, &links)
	require.NoError(t, err)
	assert.Empty(t, links["next"])
	assert.NotEmpty(t, links["prev"])

	assert.Len(t, externalInitiators, 1)
	assert.Equal(t, strconv.FormatInt(eiFoo.ID, 10), externalInitiators[0].ID)
	assert.Equal(t, eiFoo.Name, externalInitiators[0].Name)
	assert.Equal(t, eiFoo.URL.String(), externalInitiators[0].URL.String())
	assert.Equal(t, eiFoo.AccessKey, externalInitiators[0].AccessKey)
	assert.Equal(t, eiFoo.OutgoingToken, externalInitiators[0].OutgoingToken)
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

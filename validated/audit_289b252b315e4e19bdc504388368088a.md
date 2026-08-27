Confirmed: `authv2.GET("/external_initiators", paginatedRequest(eia.Index))` at [1](#0-0)  has no `RequiresEditRole`/`RequiresRunRole` wrapper, while `Create`/`Destroy` on the same resource require `auth.RequiresEditRole`. Any authenticated user (including `UserRoleView`) passes only the base `auth.Authenticate` middleware and reaches `ExternalInitiatorsController.Index`, which returns `AccessKey` and `OutgoingToken` for every EI record via `presenters.ExternalInitiatorResource` [2](#0-1) .

### Title
Missing role check on `GET /v2/external_initiators` allows view-role users to read External Initiator `AccessKey`/`OutgoingToken` - ([File: core/web/router.go])

### Summary
The `/v2/external_initiators` listing route is registered without any role-restriction wrapper, unlike its sibling `Create`/`Destroy` routes which are gated behind `auth.RequiresEditRole`. Because `auth.Authenticate` alone does not check role, any authenticated user with `UserRoleView` can call the endpoint and receive every External Initiator's `AccessKey` and `OutgoingToken`.

### Finding Description
`v2Routes` registers the External Initiator endpoints as:
```go
authv2.GET("/external_initiators", paginatedRequest(eia.Index))
authv2.POST("/external_initiators", auth.RequiresEditRole(eia.Create))
authv2.DELETE("/external_initiators/:Name", auth.RequiresEditRole(eia.Destroy))
``` [1](#0-0) 

`authv2` only enforces `auth.Authenticate(..., AuthenticateByToken, AuthenticateBySession)` [3](#0-2) , which sets the authenticated user in context but performs no role check itself. Role enforcement in this codebase is opt-in per-route via wrappers like `auth.RequiresRunRole`/`auth.RequiresEditRole`/`auth.RequiresAdminRole`, which explicitly reject `UserRoleView` (and `UserRoleRun` for edit-gated routes) [4](#0-3) . `eia.Index` is wrapped only in `paginatedRequest`, with no such role check, so a `UserRoleView` session/token reaches `ExternalInitiatorsController.Index` [5](#0-4) , which queries all rows via `BridgeORM().ExternalInitiators` [6](#0-5)  and serializes each into `presenters.ExternalInitiatorResource`, exposing `AccessKey` and `OutgoingToken` fields in the JSON:API response [2](#0-1) . The existing test `TestExternalInitiatorsController_Index` confirms these fields are returned in the paginated list response [7](#0-6) .

Note: the incoming `Secret`, `Salt`/`HashedSecret`, and `OutgoingSecret` are NOT included in `ExternalInitiatorResource` (only present in `ExternalInitiatorAuthentication`, returned solely on `Create`), so the plaintext incoming/outgoing secrets are not disclosed by this endpoint. What is disclosed to an under-privileged view-role user is the EI's `AccessKey` (the incoming identifier/username-equivalent used together with the secret to authenticate as that EI) and `OutgoingToken` (used by the node to authenticate outbound webhook calls to the EI, which a receiver-side integration may treat as a shared credential).

### Impact Explanation
A view-role user — the lowest privilege authenticated role — can enumerate all configured External Initiators and obtain their `AccessKey`/`OutgoingToken` values, which they should not be entitled to see given that mutation of this resource requires edit role. This is an authorization/role-check gap resulting in disclosure of initiator identifiers/tokens to an under-privileged principal, matching a "broken access control / privilege boundary violation" impact class rather than full secret compromise (since salted secrets/outgoing secret are excluded from this presenter).

### Likelihood Explanation
Exploitation only requires a valid view-role session or API token — no special conditions, race, or additional bypass needed. `GET /v2/external_initiators` is trivially and repeatably reachable by any authenticated low-privilege user once role assignment grants "view".

### Recommendation
Wrap the route with the same role requirement as the mutating endpoints, e.g.:
```go
authv2.GET("/external_initiators", auth.RequiresEditRole(paginatedRequest(eia.Index)))
```
so that listing requires at least edit role, consistent with `Create`/`Destroy`.

### Proof of Concept
Extend `TestExternalInitiatorsController_Index` (or add a new test) in `core/web/external_initiators_controller_test.go`:
1. Create an application and insert an `ExternalInitiator` via `cltest.MustInsertExternalInitiatorWithOpts`.
2. Create an HTTP client authenticated with `UserRoleView` (see existing helpers used for other role-gated endpoint tests, e.g. patterns in `core/web/bridge_types_controller_test.go`/`user_controller_test.go` for view-role clients).
3. `GET /v2/external_initiators` with the view-role client.
4. Assert current (vulnerable) behavior: `http.StatusOK` is returned and the response body contains the EI's `AccessKey`/`OutgoingToken` — demonstrating the route is reachable and discloses data to a view-role principal.
5. After applying the fix (`auth.RequiresEditRole`), assert the same request now returns `http.StatusUnauthorized`, matching the behavior of `POST /v2/external_initiators` and `DELETE /v2/external_initiators/:Name` for view-role users.

### Citations

**File:** core/web/router.go (L245-248)
```go
	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
```

**File:** core/web/router.go (L263-266)
```go
		eia := ExternalInitiatorsController{app}
		authv2.GET("/external_initiators", paginatedRequest(eia.Index))
		authv2.POST("/external_initiators", auth.RequiresEditRole(eia.Create))
		authv2.DELETE("/external_initiators/:Name", auth.RequiresEditRole(eia.Destroy))
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

**File:** core/bridges/orm.go (L212-225)
```go
func (o *orm) ExternalInitiators(ctx context.Context, offset int, limit int) (initiators []ExternalInitiator, count int, err error) {
	err = o.transact(ctx, true, func(tx *orm) error {
		if err = tx.ds.GetContext(ctx, &count, "SELECT COUNT(*) FROM external_initiators"); err != nil {
			return pkgerrors.Wrap(err, "ExternalInitiators failed to get count")
		}

		sql := `SELECT * FROM external_initiators ORDER BY name asc LIMIT $1 OFFSET $2;`
		if err = tx.ds.SelectContext(ctx, &initiators, sql, limit, offset); err != nil {
			return pkgerrors.Wrap(err, "ExternalInitiators failed to load external_initiators")
		}
		return nil
	})
	return
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

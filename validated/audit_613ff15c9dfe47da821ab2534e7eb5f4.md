### Title
Unrestricted `GET /v2/external_initiators` discloses AccessKey/OutgoingToken for every External Initiator to any authenticated user, including the lowest-privilege View role - ([File: core/web/external_initiators_controller.go])

### Summary
`ExternalInitiatorsController.Index` returns the full `ExternalInitiatorResource` (including `AccessKey` and `OutgoingToken`) for every External Initiator record in the node, paginated by attacker-controlled `size`/`page`. Unlike `POST`/`DELETE /v2/external_initiators`, which are wrapped in `auth.RequiresEditRole`, the `GET` route has no role gate beyond generic authentication, so a `UserRoleView` (or `UserRoleRun`) account can enumerate every other EI's incoming AccessKey and outgoing token.

### Finding Description
The route is registered without any privilege wrapper: [1](#0-0) [2](#0-1) 

```
eia := ExternalInitiatorsController{app}
authv2.GET("/external_initiators", paginatedRequest(eia.Index))
authv2.POST("/external_initiators", auth.RequiresEditRole(eia.Create))
authv2.DELETE("/external_initiators/:Name", auth.RequiresEditRole(eia.Destroy))
```

`Index` simply pages through `BridgeORM().ExternalInitiators` and returns every row as a resource, with no scoping to the caller's own identity: [3](#0-2) [4](#0-3) 

The presenter includes the raw `AccessKey` and `OutgoingToken` fields (secrets used to authenticate as that EI, or to accept the node's outgoing callback), not just metadata: [5](#0-4) 

The project's own RBAC test table explicitly documents that this route is reachable by `viewOnlyAllowed: true` (and `editMinimalAllowed`/`EditAllowed`), unlike the `POST`/`DELETE` variants which require edit role: [6](#0-5) 

The functional test confirms the response body contains `AccessKey` and `OutgoingToken` for every listed EI: [7](#0-6) 

Regarding the specific "EI session" framing in the question: `AuthenticateExternalInitiator` (which maps an EI accesskey/secret pair to a synthetic `UserRoleRun` session) exists in `core/web/auth/auth.go` but is **not wired into the `/v2` authenticated route group** used by `GET /v2/external_initiators` — that group only uses `AuthenticateByToken` and `AuthenticateBySession`: [8](#0-7) [9](#0-8) 

So a holder of only EI credentials (no real user account) cannot directly hit this admin route with those credentials — `AuthenticateExternalInitiator` is effectively dead/unused code for this route. The actual exploitable path is a real, low-privilege **user account** (View role, the minimum role that can log in) hitting `GET /v2/external_initiators`, which is not gated the same way write operations are.

### Impact Explanation
Any authenticated user, including one restricted to `UserRoleView` (read-only, cannot create/delete anything), can dump the `AccessKey` and `OutgoingToken` of **every** External Initiator configured on the node. `AccessKey`+`Secret` are the credentials external systems use to trigger job runs as that EI; `OutgoingToken` is used to validate the node's outgoing webhook callbacks. Exposure of `AccessKey`/`OutgoingToken` for all EIs to a low-privilege viewer is a credential/secret-disclosure issue that can facilitate unauthorized job-run triggering or webhook impersonation if the corresponding secret is also exposed or guessable elsewhere (note: `Secret`/`OutgoingSecret` are not returned by `Index`, only by `Create`, which limits full credential reconstruction from this endpoint alone).

### Likelihood Explanation
Requires only a valid, low-privilege (`View`) user account/session or API token on the target node — no admin/edit rights, no host access. The endpoint is directly reachable at `GET /v2/external_initiators?size=<n>&page=<n>` and is fully deterministic/repeatable; the RBAC test table confirms this access level is the current, tested (i.e., intentional in the current code) behavior rather than a one-off bug.

### Recommendation
Wrap `GET /v2/external_initiators` with `auth.RequiresEditRole` (matching `POST`/`DELETE`) or, at minimum, redact `AccessKey`/`OutgoingToken` from the list resource for callers without edit/admin role, so only authorized operators can view EI credentials in bulk.

### Proof of Concept
1. In `core/web/external_initiators_controller_test.go`, extend `TestExternalInitiatorsController_Index` (or add a new test) to authenticate as a `UserRoleView` client via `app.NewHTTPClient(&cltest.User{Role: sessions.UserRoleView})` (pattern used elsewhere in RBAC tests) instead of the default admin client.
2. Insert two EIs via `cltest.MustInsertExternalInitiatorWithOpts` with distinct `AccessKey`/`OutgoingToken`.
3. Call `client.Get("/v2/external_initiators?size=1000")` as the View-role client.
4. Assert current (vulnerable) behavior: `http.StatusOK` and response body contains both EIs' `AccessKey` and `OutgoingToken` fields (as in existing assertions at lines 104-109 of the controller test).
5. Expected fixed behavior: assert `http.StatusUnauthorized`/`403` for `View` role, matching the pattern already used for `POST`/`DELETE /v2/external_initiators` in `core/web/auth/auth_test.go`.

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

**File:** core/web/presenters/external_initiators.go (L57-65)
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

**File:** core/web/auth/auth.go (L116-151)
```go
// AuthenticateExternalInitiator authenticates an external initiator request.
//
// Implements authMethod
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

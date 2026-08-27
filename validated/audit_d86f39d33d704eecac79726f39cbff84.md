### Title
Missing role check on `GET /v2/external_initiators` discloses `AccessKey`/`OutgoingToken` for every external initiator to any authenticated user - ([File: core/web/router.go, core/web/external_initiators_controller.go, core/web/presenters/external_initiators.go])

### Summary
`ExternalInitiatorsController.Index` is registered without any `auth.RequiresEditRole`/`auth.RequiresAdminRole`/`auth.RequiresRunRole` wrapper, unlike its sibling `Create`/`Destroy` routes, so any authenticated principal - including a `view`-role user, a `run`-role user, or an external-initiator-derived session (which is auto-assigned `UserRoleRun` in `AuthenticateExternalInitiator`) - can call it and receive the full, unfiltered list of external initiators with their `AccessKey` and `OutgoingToken` values.

### Finding Description
The route table wires `/v2/external_initiators` as follows: [1](#0-0) 
GET has no role wrapper, while POST/DELETE require `auth.RequiresEditRole`. `Index` simply loads and paginates all initiators with no per-caller filtering: [2](#0-1) 
`presenters.NewExternalInitiatorResource` serializes `AccessKey` and `OutgoingToken` for every record returned: [3](#0-2) 
This is confirmed by the existing test, which shows both `foo`'s and `bar`'s `AccessKey`/`OutgoingToken` are returned in plaintext to the caller of `GET /v2/external_initiators`: [4](#0-3) 

Because `Index` is reachable via the generic `authv2` group (any of session or API-token auth, `AuthenticateBySession`/`AuthenticateByToken`) without any role gate, `RequiresRunRole`/`RequiresEditRole`/`RequiresAdminRole` (defined in `core/web/auth/auth.go`) are never invoked to check the caller's `UserRole` (`view`, `run`, `edit`, `admin`), so a `view`-role user - which is only supposed to have read access to non-sensitive data - or a `run`-role user/API token can enumerate every external initiator's `AccessKey` and `OutgoingToken`, none of which are scoped to "their own" initiator (there is no per-user ownership concept for external initiators at all - they are node-wide credentials created by an edit/admin user).

### Impact Explanation
`AccessKey` is the public identifier an external initiator presents (paired with a secret validated via `AuthenticateExternalInitiator`) to authenticate into the node's EI-authenticated endpoints, and `OutgoingToken` is used by the node when calling back out to the external initiator's webhook. Disclosure of these values to a low-privilege (`view`/`run`) authenticated user lets that user enumerate initiator identifiers and outgoing tokens for initiators unrelated to any credential they were issued. While the corresponding `HashedSecret`/`Salt` for full impersonation of inbound auth are not exposed by this presenter, the `OutgoingToken` disclosure alone constitutes credential exposure of another entity's secret data, and combined with the lack of any authorization scoping this represents a broken access-control / information-disclosure issue (Chainlink bounty class: sensitive information disclosure / broken authorization).

### Likelihood Explanation
Trivial and fully repeatable: any valid session cookie or API token of any role (`view`, `run`, `edit`, `admin`), including a token intentionally provisioned with the least-privileged `view` or `run` role, or a session automatically stamped `UserRoleRun` via `AuthenticateExternalInitiator`, is sufficient to hit `GET /v2/external_initiators` and receive the full list with `AccessKey`/`OutgoingToken` for every initiator in the node, not just ones the caller is entitled to see. No race conditions or special timing are required.

### Recommendation
Wrap the `GET /external_initiators` route with the same authorization requirement as the mutating routes (at minimum `auth.RequiresEditRole`, matching `Create`/`Destroy`), and/or redact `AccessKey`/`OutgoingToken`/`OutgoingSecret` from `presenters.ExternalInitiatorResource` for list responses so that sensitive credential material is never returned outside of the one-time `ExternalInitiatorAuthentication` response issued at creation time.

### Proof of Concept
1. In `core/web/external_initiators_controller_test.go`, add a handler-level test that creates two users/tokens with `sessions.UserRoleView` and `sessions.UserRoleRun` respectively (see existing helpers for creating users/tokens with a given role in `cltest`).
2. Insert two external initiators via `cltest.MustInsertExternalInitiatorWithOpts` (as in `TestExternalInitiatorsController_Index`).
3. Using the `view`-role and `run`-role authenticated clients (not `edit`/`admin`), call `client.Get("/v2/external_initiators")`.
4. Assert the current behavior: response is `200 OK` and the parsed `[]presenters.ExternalInitiatorResource` includes `AccessKey` and `OutgoingToken` for both initiators, proving a `view`/`run`-role identity can read credential fields it should not be entitled to.
5. Expected/fixed behavior: request should be rejected with `401`/`403` for `view`/`run` roles (matching `RequiresEditRole` semantics), or the response payload should omit `AccessKey`/`OutgoingToken`.

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

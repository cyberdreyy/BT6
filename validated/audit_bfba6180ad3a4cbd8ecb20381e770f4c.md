### Title
GET /v2/external_initiators lacks role restriction, exposing AccessKey to any authenticated user regardless of role - ([File: core/web/router.go])

### Summary
The `ExternalInitiatorsController.Index` route is registered without any `auth.RequiresEditRole`/`auth.RequiresRunRole`/`auth.RequiresAdminRole` wrapper, unlike the sibling `Create`/`Destroy` routes on the same resource. As a result, any authenticated session or API token — including the lowest-privilege "view" role — can enumerate all external initiators and read their plaintext `AccessKey` field via `presenters.ExternalInitiatorResource`.

### Finding Description
`core/web/router.go` registers:
```go
authv2.GET("/external_initiators", paginatedRequest(eia.Index))
authv2.POST("/external_initiators", auth.RequiresEditRole(eia.Create))
authv2.DELETE("/external_initiators/:Name", auth.RequiresEditRole(eia.Destroy))
``` [1](#0-0) 

The `authv2` group only requires successful `AuthenticateByToken`/`AuthenticateBySession` — i.e. any valid, even lowest-privileged, user credential — with no minimum role check applied specifically to `Index`. `ExternalInitiatorsController.Index` builds `presenters.ExternalInitiatorResource` from ORM records and returns it directly: [2](#0-1) 

`ExternalInitiatorResource` explicitly serializes `AccessKey` with `json:"accessKey"` (it does not include `Salt` or `HashedSecret`, so the hashed secret material is not leaked): [3](#0-2) 

This is confirmed by the existing test, which asserts the plaintext `AccessKey` is returned in the JSON body of the `/v2/external_initiators` listing endpoint with no role gating applied: [4](#0-3) 

Root cause: `bridges.NewExternalInitiator` (`core/bridges/external_initiator.go:38-57`) hashes+salts only the `Secret`, while `AccessKey` is stored and returned in plaintext by design (it functions like a username, not a secret) — that design choice is not itself a bug. The bug is that the `Index` route, which returns this `AccessKey`, is reachable by any authenticated principal without any role check, unlike the `Create`/`Destroy` routes for the same resource which correctly require Edit role.

### Impact Explanation
This is an authorization-gap information disclosure: a "view"-role user (or any authenticated token/session, since no role check exists on this route) — who under Chainlink's role model should have read-only, non-privileged access — can retrieve the `AccessKey` of every external initiator (EI) registered on the node. `AccessKey` is one of the two credentials needed to authenticate as an EI and trigger job runs (`web/auth/auth.go` `AuthenticateExternalInitiator`, `core/bridges/external_initiator.go` `AuthenticateExternalInitiator`). Disclosure of `AccessKey` narrows the attack surface for anyone attempting to impersonate an EI to guessing/obtaining only the `Secret`; it also breaks the intended separation between roles (view vs. edit) for a resource whose write operations are correctly Edit-role-gated. This maps to a "broken access control / unauthorized information disclosure of authentication material" impact class — not a full authentication bypass, since the 32-byte random `Secret` is never disclosed and brute-forcing it via `AuthenticateExternalInitiator`'s constant-time hash comparison is computationally infeasible.

### Likelihood Explanation
Any user with a valid session cookie or API token — the minimum credential any registered node user of any role can hold — can call `GET /v2/external_initiators` directly (or via the `IndexExternalInitiators` CLI/`Shell` command) with no additional privilege required. This is trivially reproducible and repeatable; no timing, race, or advanced conditions are needed. The only prerequisite is that at least one EI already exists.

### Recommendation
Add an explicit role wrapper to the `Index` route consistent with `Create`/`Destroy`, e.g. `auth.RequiresEditRole(eia.Index)` (or a dedicated `RequiresRunRole`/`RequiresAdminRole` depending on the desired minimum privilege), so that only users with sufficient privilege can list `AccessKey` values. Additionally consider whether `AccessKey` needs to be returned at all to non-admin roles, or should be presented only in a redacted/partial form in list responses.

### Proof of Concept
Go handler-level integration test plan (extending `core/web/external_initiators_controller_test.go`):
1. Start a `TestApplication` with `ExternalInitiatorsEnabled = true`.
2. Insert an EI via `cltest.MustInsertExternalInitiatorWithOpts`, capturing `eiFoo.AccessKey`.
3. Create a client authenticated as a "view"-role (or lowest-privilege, non-edit) user, e.g. via `app.NewHTTPClient` configured with a user session created with `sessions.UserRoleView`.
4. Call `client.Get("/v2/external_initiators")`.
5. Assert response status is `200 OK` (demonstrating no role check blocks it) and parse the JSON-API body into `[]presenters.ExternalInitiatorResource`.
6. Assert `externalInitiators[0].AccessKey == eiFoo.AccessKey` is non-empty and matches the stored value, proving the low-privilege caller received the AccessKey.
7. As a control, assert the raw response body does NOT contain `hashedSecret` or `salt` substrings (confirming only `AccessKey`, not the hashed secret, leaks), to scope the finding precisely as an authorization/role-gating gap on the `Index` route rather than a hash-disclosure issue.

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

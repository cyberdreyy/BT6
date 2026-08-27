### Title
Unrestricted GET /v2/external_initiators leaks `OutgoingToken` for all external initiators to any authenticated user, including Run-role - ([File: core/web/external_initiators_controller.go])

### Summary
`ExternalInitiatorsController.Index` returns `presenters.ExternalInitiatorResource` for every external initiator in the node, and the route is mounted without any role wrapper (`auth.RequiresEditRole`/`RequiresRunRole`), unlike `Create`/`Destroy` on the same resource. `ExternalInitiatorResource` includes the `OutgoingToken` field, so any authenticated node API user — including a minimal Run-role user — can enumerate `OutgoingToken` values belonging to external initiators they did not create.

### Finding Description
The router wires `/v2/external_initiators` as: [1](#0-0) 
`Index` has no role guard, while `Create` and `Destroy` are wrapped in `auth.RequiresEditRole`. Any user passing the outer `auth.Authenticate(... AuthenticateByToken, AuthenticateBySession)` middleware — View, Run, Edit, or Admin role — can call `Index`.

`Index` builds its response using the presenter: [2](#0-1) 

The presenter used for listing exposes `OutgoingToken` in plaintext: [3](#0-2) 

This is corroborated by the existing integration test, which explicitly asserts that `Index` returns the plaintext `OutgoingToken` for each initiator: [4](#0-3) 

Note the actual `ExternalInitiator` model also stores `Salt`, `HashedSecret`, and `OutgoingSecret`: [5](#0-4) 
but `ExternalInitiatorResource` (used by `Index`) does **not** include `Salt`, `HashedSecret`, or `OutgoingSecret` — only `OutgoingToken` is leaked. Those three fields are only returned once, at creation time, via the separate `ExternalInitiatorAuthentication` presenter to the creator: [6](#0-5) 

So the exploitable over-exposure is narrower than the full claim in the question: `OutgoingToken` is disclosed cross-user with no role restriction; `OutgoingSecret`, `HashedSecret`, and `Salt` are not present in the `Index` response and are not disclosed by this path.

### Impact Explanation
`OutgoingToken` is a credential the Chainlink node uses to authenticate itself when it calls back out to the external initiator's webhook. Disclosure of another initiator's `OutgoingToken` to a low-privileged (Run-role) node API user allows that user to impersonate the node when talking to the external initiator's callback endpoint, or otherwise misuse a secret they should not have access to. This matches the "sensitive credential/secret disclosure" bounty impact class, though it is more limited than a full authentication-bypass (it does not expose the `HashedSecret`/`Salt` needed to forge an *inbound* EI-authenticated request against the node, nor `OutgoingSecret`).

### Likelihood Explanation
Any authenticated node API session or API token — regardless of role (`view`, `run`, `edit`, `admin`) — can hit `GET /v2/external_initiators` since `Index` has no `RequiresEditRole`/`RequiresRunRole` wrapper, unlike `Create`/`Destroy`. This requires only a valid low-privilege node credential (e.g., a Run-role account), which is a realistic, low-bar precondition, and the leak is repeatable/enumerable across pages for every initiator on the node.

### Recommendation
Wrap `authv2.GET("/external_initiators", ...)` with an appropriate role check (at minimum `auth.RequiresEditRole`, consistent with `Create`/`Destroy`), and remove `OutgoingToken` from `ExternalInitiatorResource` (or redact/mask it) since list views should not need to re-expose outgoing credentials after initial creation.

### Proof of Concept
1. As an Admin/Edit-role user, create two external initiators `EI-A` and `EI-B` via `POST /v2/external_initiators`, capturing each returned `OutgoingToken`.
2. Create a Run-role API session/token (`sessions.UserRoleRun`).
3. As the Run-role user, call `GET /v2/external_initiators`.
4. Assert the HTTP status is `200 OK` (not `401`/`403`) even though the role is Run.
5. Parse the JSON:API response into `[]presenters.ExternalInitiatorResource` and assert that `OutgoingToken` for both `EI-A` and `EI-B` (entries not "owned" by the requesting Run-role session) is present and matches the values captured at creation — demonstrating cross-user secret disclosure to a role that has no edit/create/destroy rights on this resource.
6. (Negative check) Confirm `Salt`, `HashedSecret`, and `OutgoingSecret` are absent from the JSON response, since `ExternalInitiatorResource` does not serialize them — narrowing the confirmed leak to `OutgoingToken` only.

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

**File:** core/web/presenters/external_initiators.go (L12-38)
```go
// ExternalInitiatorAuthentication includes initiator and authentication details.
type ExternalInitiatorAuthentication struct {
	Name           string        `json:"name,omitempty"`
	URL            models.WebURL `json:"url"`
	AccessKey      string        `json:"incomingAccessKey,omitempty"`
	Secret         string        `json:"incomingSecret,omitempty"`
	OutgoingToken  string        `json:"outgoingToken,omitempty"`
	OutgoingSecret string        `json:"outgoingSecret,omitempty"`
}

// NewExternalInitiatorAuthentication creates an instance of ExternalInitiatorAuthentication.
func NewExternalInitiatorAuthentication(
	ei bridges.ExternalInitiator,
	eia auth.Token,
) *ExternalInitiatorAuthentication {
	var result = &ExternalInitiatorAuthentication{
		Name:           ei.Name,
		AccessKey:      ei.AccessKey,
		Secret:         eia.Secret,
		OutgoingToken:  ei.OutgoingToken,
		OutgoingSecret: ei.OutgoingSecret,
	}
	if ei.URL != nil {
		result.URL = *ei.URL
	}
	return result
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

**File:** core/bridges/external_initiator.go (L22-34)
```go
type ExternalInitiator struct {
	ID             int64
	Name           string
	URL            *models.WebURL
	AccessKey      string
	Salt           string
	HashedSecret   string
	OutgoingSecret string
	OutgoingToken  string

	CreatedAt time.Time
	UpdatedAt time.Time
}
```

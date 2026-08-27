### Title
GET /v2/external_initiators lacks `RequiresEditRole`, allowing view-role users to enumerate `AccessKey` and `OutgoingToken` external-initiator credentials - ([File: core/web/router.go])

### Finding Description
`v2Routes` wires `GET /v2/external_initiators` behind only `paginatedRequest(eia.Index)`, with no `auth.RequiresEditRole` wrapper, while the sibling `POST` and `DELETE` routes are explicitly gated with `auth.RequiresEditRole`: [1](#0-0) . Any authenticated session/token — including a view-role user — can therefore call this endpoint since the only gate in front of it is the generic `authv2` group requiring session/token authentication, not role level: [2](#0-1) .

`ExternalInitiatorsController.Index` fetches all external initiators from `BridgeORM().ExternalInitiators` and serializes each with `presenters.NewExternalInitiatorResource`: [3](#0-2) . That resource type includes `AccessKey` and `OutgoingToken` fields verbatim, alongside `Name` and `URL`: [4](#0-3) .

The raw plaintext `Secret` (the incoming HMAC secret returned only once, at creation time via `ExternalInitiatorAuthentication`) is **not** part of `ExternalInitiatorResource` and is therefore not exposed by this list endpoint: [5](#0-4) . So the proof's specific concern ("raw Secret field is exposed") does not materialize — the incoming secret is properly confined to the one-time creation response.

However, `AccessKey` (the incoming-request identifier paired with the secret) and `OutgoingToken` (used by the node to authenticate its outbound webhook calls to the external initiator's URL) are returned to any authenticated caller regardless of role, in contrast to the explicit `RequiresEditRole` gate on create/delete. This is an authorization-exactness inconsistency: the same credential material class that requires edit-role to create/delete can be listed by a view-role user.

### Impact Explanation
A view-role (or restricted) authenticated user can enumerate all configured external initiators' `Name`, `URL`, `AccessKey`, and `OutgoingToken` via `GET /v2/external_initiators`. `OutgoingToken` is a credential the node uses to authenticate itself when calling the external initiator's webhook URL; disclosure lets a lower-privileged user learn credential material intended to be edit-role-gated, and combined with the disclosed `URL`, could be used to impersonate the node's outbound calls to that external service or to fingerprint external-initiator infrastructure. This is a scoped, moderate credential-exposure issue (`AUTHORIZATION_EXACTNESS` violation), not full secret disclosure — the incoming `Secret` itself remains confined to the create-response and is not retrievable via this route.

### Likelihood Explanation
Requires only a view-role (or any authenticated, non-edit) session or API token — the minimum precondition described in the question. The request is a single unauthenticated-role-check GET call (`GET /v2/external_initiators?size=&page=`), fully repeatable, and requires no special timing or race condition.

### Recommendation
Wrap `authv2.GET("/external_initiators", ...)` with `auth.RequiresEditRole` (or introduce a redacted presenter that omits `AccessKey`/`OutgoingToken` for view-role callers) so listing external initiators requires the same privilege level as creating/deleting them, matching the RBAC intent already applied to the bridge_types/external_initiators write routes.

### Proof of Concept
Go handler-level integration test (extending `external_initiators_controller_test.go`):
1. Create an app with an authentication provider seeding a view-role session/token and an edit-role session/token.
2. Using edit-role credentials, `POST /v2/external_initiators` to create one external initiator; capture its `AccessKey`/`OutgoingToken`.
3. Using the view-role session, call `GET /v2/external_initiators?size=50&page=1`.
4. Assert: current behavior returns HTTP 200 with a JSON body whose `data[].attributes.accessKey` and `data[].attributes.outgoingToken` match the values created in step 2 — expected/fixed behavior should return HTTP 403 (`auth.RequiresEditRole` rejection) for the view-role session.
5. Additionally assert `incomingSecret` is never present in the `Index` response body (already true), to confirm the finding is scoped to `AccessKey`/`OutgoingToken` exposure, not the incoming secret.

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

**File:** core/web/presenters/external_initiators.go (L12-20)
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

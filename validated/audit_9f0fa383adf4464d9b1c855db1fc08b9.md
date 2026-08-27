### Title
Missing role check on `GET /v2/external_initiators` leaks `AccessKey` and `OutgoingToken` secrets to any authenticated session including view-role users - ([File: core/web/external_initiators_controller.go])

### Summary
`ExternalInitiatorsController.Index` returns `presenters.ExternalInitiatorResource` for every external initiator stored on the node, including the `AccessKey` and `OutgoingToken` fields, and the route is wired without any role gate. While `Create`/`Destroy` for external initiators require `auth.RequiresEditRole`, `Index` only requires being an authenticated session/token of any role (view, run, edit, admin), so a minimally-privileged view-role user can enumerate all external initiators' secret material.

### Finding Description
The route registration in `core/web/router.go` shows the asymmetry: [1](#0-0) 
`Index` has no `auth.RequiresEditRole`/`RequiresAdminRole` wrapper, unlike `Create` and `Destroy`.

`Index` fetches all external initiators from the ORM (no per-caller filtering) and maps each to the full resource: [2](#0-1) 

The presenter includes both `AccessKey` and `OutgoingToken` in the serialized JSON response: [3](#0-2) 

`AccessKey` is the identifier half of the credential pair the node uses to authenticate incoming webhook/EI-triggered requests, and `OutgoingToken` is the bearer credential the node itself presents when calling out to the external initiator's service. Neither the `bridges.ExternalInitiator` model nor its ORM query (`ExternalInitiators(ctx, offset, size)`) scopes rows by creator/user — this confirms external initiators are node-wide resources, not per-user tenants, so any session authorized to hit `/v2/external_initiators` sees every initiator registered on the node regardless of who created it.

### Impact Explanation
Any authenticated caller — including a view-role session/API token, which is meant to be read-only and non-sensitive — can retrieve `AccessKey` and `OutgoingToken` values for all external initiators on the node. This maps to Chainlink's "key or secret disclosure" bounty class: it breaks the intended privilege separation between view and edit/admin roles for a sensitive credential-bearing resource, even though full request forgery would additionally require the (never re-exposed) `Secret`/`OutgoingSecret` values.

### Likelihood Explanation
Exploitation requires only a valid view-role session or API token — the lowest privilege tier that can authenticate to the node API at all — and a single unauthenticated-role-gated `GET /v2/external_initiators` call. This is trivially repeatable and requires no special conditions beyond having any authenticated credential on the node.

### Recommendation
Wrap `authv2.GET("/external_initiators", ...)` with `auth.RequiresEditRole` (or `RequiresAdminRole`) to match the protection level of `Create`/`Destroy`, and/or strip `AccessKey`/`OutgoingToken` from `presenters.ExternalInitiatorResource` used by `Index`, only exposing them at creation time as is already done via `ExternalInitiatorAuthentication`.

### Proof of Concept
1. In `core/web/external_initiators_controller_test.go`, create two external initiators (e.g., via `Create` as an edit-role client).
2. Establish a session/API token with `sessions.ViewRole`.
3. Call `GET /v2/external_initiators` with the view-role client.
4. Assert HTTP 200 (not 401/403) and that the response body's `data[].attributes.accessKey` / `outgoingToken` fields are non-empty and match the values generated for both external initiators, proving a view-role caller can read secret fields for initiators it did not create.

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

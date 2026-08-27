### Title
Missing role check on `GET /v2/external_initiators` (Index) exposes `OutgoingToken` to any authenticated user regardless of role - ([File: core/web/external_initiators_controller.go])

### Summary
`ExternalInitiatorsController.Create` correctly returns the one-time secret bundle (`Secret`, `AccessKey`, `OutgoingToken`, `OutgoingSecret`) via `presenters.NewExternalInitiatorAuthentication`, and this plaintext bundle is never persisted or reused elsewhere. However, `Index` serializes external initiators with `presenters.NewExternalInitiatorResource`, which includes `OutgoingToken` in its JSON output, and the `GET /v2/external_initiators` route has no `RequiresEditRole`/`RequiresViewRole`/`RequiresAdminRole` wrapper, unlike `Create`/`Destroy` on the same resource.

### Finding Description
`ExternalInitiatorsController.Create` builds the response from `auth.Token` (`eia`) and the freshly created `bridges.ExternalInitiator` (`ei`), returning `Secret`, `AccessKey`, `OutgoingToken`, and `OutgoingSecret` [1](#0-0) . This plaintext `Secret` is never stored — only `HashedSecret`/`Salt` are persisted on the `ExternalInitiator` struct [2](#0-1) , so it structurally cannot reappear via `Index`.

`Index` instead uses `presenters.NewExternalInitiatorResource`, which only exposes `Name`, `URL`, `AccessKey`, `OutgoingToken`, `CreatedAt`, `UpdatedAt` — `HashedSecret`, `Salt`, and `OutgoingSecret` are correctly excluded [3](#0-2) . So `HashedSecret`, `Salt`, the plaintext incoming `Secret`, and `OutgoingSecret` do **not** leak through `Index`.

However, `OutgoingToken` **is** included in `ExternalInitiatorResource` and is returned on every call to `Index` [4](#0-3) . Critically, the route registration shows `Create`/`Destroy` are wrapped with `auth.RequiresEditRole`, while `Index` has no role wrapper at all — only the generic `authv2` authentication (token or session) applies:
```
authv2.GET("/external_initiators", paginatedRequest(eia.Index))
authv2.POST("/external_initiators", auth.RequiresEditRole(eia.Create))
authv2.DELETE("/external_initiators/:Name", auth.RequiresEditRole(eia.Destroy))
``` [5](#0-4) 

This means any authenticated user of any role (including view-only) can call `GET /v2/external_initiators` and receive every external initiator's `OutgoingToken`.

### Impact Explanation
`OutgoingToken` is half of the outgoing credential pair (`OutgoingToken`/`OutgoingSecret`) used by the node to authenticate itself when calling out to an external initiator's webhook. Exposing it to lower-privileged authenticated users constitutes cross-user disclosure of credential material tied to a higher-privileged action (creating/managing external initiators requires edit role), matching a low/medium "sensitive information disclosure" bounty class. It is **not** a full compromise: `OutgoingSecret`, `HashedSecret`, `Salt`, and the plaintext incoming `Secret` are correctly redacted and never appear outside the one-time `Create` response, so the specific claim that `eia.Secret`/`OutgoingSecret`/`HashedSecret`/`Salt` leak via `Index` is **not substantiated** by the code.

### Likelihood Explanation
Any authenticated user with a valid session or API token (any role, e.g., "view" role) can call `GET /v2/external_initiators` with no additional privilege required, since `Index` lacks a role-restriction wrapper present on the sibling `Create`/`Destroy` routes. This is trivially repeatable.

### Recommendation
Add a role wrapper (e.g., `auth.RequiresEditRole` or at minimum `auth.RequiresViewRole` if such a role tier is intended for read-only access) to the `GET /v2/external_initiators` route, and/or remove `OutgoingToken` from `presenters.ExternalInitiatorResource` (or mask it) so that Index responses do not expose outgoing-authentication material to less-privileged authenticated callers.

### Proof of Concept
1. Unit test on `presenters.NewExternalInitiatorResource`: construct a `bridges.ExternalInitiator` with populated `HashedSecret`, `Salt`, `OutgoingSecret`, `OutgoingToken`; assert the resulting JSON does not contain `HashedSecret`/`Salt`/`OutgoingSecret` keys (pass) but does contain `outgoingToken` (documents the exposure).
2. Handler-level integration test: create a session/API-token authenticated as a low-privileged ("view") user; call `GET /v2/external_initiators`; assert HTTP 200 is returned (rather than 401/403), demonstrating the missing role check versus `POST/DELETE` on the same resource which return 401/403 for the same user.

#### Note
Only the `OutgoingToken` exposure via the unauthorized-role `Index` route is substantiated by the code; the broader claim in the question that plaintext `Secret`, `HashedSecret`, `Salt`, or `OutgoingSecret` leak via `Index` is not supported — `presenters.ExternalInitiatorResource` explicitly excludes those fields.

### Citations

**File:** core/web/presenters/external_initiators.go (L22-38)
```go
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

**File:** core/bridges/external_initiator.go (L21-34)
```go
// ExternalInitiator represents a user that can initiate runs remotely
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

**File:** core/web/router.go (L263-266)
```go
		eia := ExternalInitiatorsController{app}
		authv2.GET("/external_initiators", paginatedRequest(eia.Index))
		authv2.POST("/external_initiators", auth.RequiresEditRole(eia.Create))
		authv2.DELETE("/external_initiators/:Name", auth.RequiresEditRole(eia.Destroy))
```

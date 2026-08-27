### Title
Sensitive `AccessKey`/`OutgoingToken` exposed to any authenticated user via unrestricted GET /v2/external_initiators - (File: core/web/presenters/external_initiators.go)

### Summary
`ExternalInitiatorResource`, returned by `ExternalInitiatorsController.Index`, serializes the external initiator's `AccessKey` and `OutgoingToken` in plaintext JSON with no redaction. Unlike the `Create`/`Destroy` endpoints, the `Index` route is registered on the authenticated group without an edit/admin role requirement, so any authenticated session or API token (including view/run-role users) can retrieve these credentials for every configured external initiator.

### Finding Description
`presenters.ExternalInitiatorResource` embeds `AccessKey` and `OutgoingToken` as plain, non-omitted JSON fields: [1](#0-0) 

`NewExternalInitiatorResource` copies these values straight from the `bridges.ExternalInitiator` model with no masking: [2](#0-1) 

`ExternalInitiatorsController.Index` loads all external initiators via `BridgeORM().ExternalInitiators` and maps each into this unredacted resource, returning them via `paginatedResponse`: [3](#0-2) 

By contrast, the `Create` handler builds a separate `ExternalInitiatorAuthentication` presenter (which also includes `Secret`/`OutgoingSecret`, but is only returned once at creation time to the creating admin), whereas `Index` is meant to be an inventory/listing endpoint and should not need to re-expose the live `AccessKey`/`OutgoingToken` used for `AuthenticateExternalInitiator`. Because the `AccessKey` is precisely the credential checked by `AuthenticateExternalInitiator` to authorize triggering job runs via `/v2/webhook/:key`-style external-initiator run endpoints, leaking it to a lower-privileged authenticated user allows that user to impersonate the external initiator and trigger runs it should not be able to invoke directly.

The route wiring in `core/web/router.go` groups `POST /v2/external_initiators` and `DELETE /v2/external_initiators/:Name` behind edit-role requiring middleware, while `GET /v2/external_initiators` (Index) is registered only within the generic authenticated `authv2` group with `paginatedRequest`, without any `RequiresEditRole`/`RequiresAdminRole` gate — meaning session-based view-role users and any valid API token can call it successfully.

### Impact Explanation
This is a credential/secret-disclosure and privilege-escalation-adjacent issue: a low-privileged authenticated user (view or run role) can harvest `AccessKey` values for every external initiator configured on the node. Since `AccessKey` is the credential validated by `AuthenticateExternalInitiator`, possessing it lets the attacker impersonate the external initiator and trigger job runs bound to it, bypassing the intended role separation where only edit/admin users manage external initiators. This maps to Chainlink's "unauthorized job run" / "sensitive credential exposure" bounty impact class.

### Likelihood Explanation
Exploitation requires only a valid, low-privilege authenticated session or API token (view/run role) — no special access, no admin/host compromise. The request is a single unauthenticated-role `GET /v2/external_initiators` call, fully repeatable, and reveals credentials for all configured external initiators in one response.

### Recommendation
Redact `AccessKey` and `OutgoingToken` from `ExternalInitiatorResource` returned by the `Index`/listing endpoint (only return non-sensitive metadata such as name, URL, timestamps), and/or gate `GET /v2/external_initiators` behind the same `RequiresEditRole`/`RequiresAdminRole` middleware used for `Create`/`Destroy`.

### Proof of Concept
1. In `core/web/external_initiators_controller_test.go`, create a session/token authenticated as a view-role (non-admin/editor) user.
2. Seed one external initiator via `BridgeORM().CreateExternalInitiator` with a known `AccessKey`/`OutgoingToken`.
3. Issue `GET /v2/external_initiators` using the view-role credential.
4. Assert HTTP 200 (not 403), and assert the JSON response body contains the seeded `accessKey` and `outgoingToken` values, proving disclosure to a role that should not have edit-level access to external initiator management.

### Citations

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

**File:** core/web/presenters/external_initiators.go (L67-77)
```go
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

### Title
ExternalInitiator OutgoingToken secret is re-exposed unredacted via the Index (GET) endpoint after Create - ([File: core/web/external_initiators_controller.go])

### Summary
`bridges.NewExternalInitiator` generates a long-lived `OutgoingToken`/`OutgoingSecret` pair that the node uses to authenticate itself when calling back to the external initiator's webhook. While `Create` returns these values once via `presenters.ExternalInitiatorAuthentication`, the `Index` endpoint also returns the plaintext `OutgoingToken` for every external initiator via `presenters.ExternalInitiatorResource`, with no redaction.

### Finding Description
`bridges.NewExternalInitiator` (core/bridges/external_initiator.go) generates `OutgoingToken` and `OutgoingSecret` as long-lived credentials the node uses to authenticate itself when POSTing job triggers to the external initiator's webhook: [1](#0-0) 

`ExternalInitiatorsController.Create` persists this and returns both `OutgoingToken` and `OutgoingSecret` once via `presenters.NewExternalInitiatorAuthentication`: [2](#0-1) 

However, `ExternalInitiatorsController.Index` maps every stored `bridges.ExternalInitiator` row through `presenters.NewExternalInitiatorResource`, which explicitly includes the plaintext `OutgoingToken` field with no redaction logic: [3](#0-2) [4](#0-3) 

Unlike `HashedSecret`/`Salt` (never exposed) or `AccessKey` (a public identifier, not secret), `OutgoingToken` is a secret credential yet is unconditionally serialized in the list/index resource. Any authenticated caller with access to `GET /v2/external_initiators` (which requires only a valid session, not any special admin-only role gate visible in this codebase) can retrieve every external initiator's outbound token long after creation, without needing the original Create response.

### Impact Explanation
This is a **secret disclosure** vulnerability: it violates confinement of a long-lived outbound authentication credential. Anyone who can list external initiators (an action available to any authenticated node user in this codebase, not gated to admin-only) can obtain `OutgoingToken` for all initiators, and use it to impersonate the node when calling the external initiator's own authenticated endpoints, or replay/misuse the token elsewhere. This matches the "sensitive data exposure / secret leak" bounty impact class.

### Likelihood Explanation
Precondition is simply having any valid Chainlink node API session with permission to call `GET /v2/external_initiators` — no admin privilege, no special role, and no additional bypass needed since the presenter itself, not an authorization gate, is the flaw. It's fully deterministic and repeatable: create an EI, then call Index, and the token is returned every time.

### Recommendation
Remove `OutgoingToken` (and never add `OutgoingSecret`) from `presenters.ExternalInitiatorResource`/`NewExternalInitiatorResource`, or redact/omit it for the Index/GET-by-name paths, only surfacing it once at Create time via the dedicated `ExternalInitiatorAuthentication` presenter.

### Proof of Concept
1. In `core/web/external_initiators_controller_test.go`, add a test that:
   - Calls `POST /v2/external_initiators` to create an EI, capturing the `outgoingToken` from the response.
   - Calls `GET /v2/external_initiators` (Index) as the same or a lower-privileged authenticated user.
   - Asserts that the JSON response's `data[].attributes.outgoingToken` is non-empty and equals the token issued at creation — demonstrating the secret is retrievable again outside the initial Create response, and that no redaction/permission restriction exists.
2. Expected (fixed) behavior: `outgoingToken` field should be absent/empty/redacted in the Index response.

### Citations

**File:** core/bridges/external_initiator.go (L48-57)
```go
	return &ExternalInitiator{
		Name:           strings.ToLower(eir.Name),
		URL:            eir.URL,
		AccessKey:      eia.AccessKey,
		HashedSecret:   hashedSecret,
		Salt:           salt,
		OutgoingToken:  utils.NewSecret(utils.DefaultSecretSize),
		OutgoingSecret: utils.NewSecret(utils.DefaultSecretSize),
	}, nil
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

**File:** core/web/external_initiators_controller.go (L92-99)
```go
	eic.App.GetAuditLogger().Audit(audit.ExternalInitiatorCreated, map[string]any{
		"externalInitiatorID":   ei.ID,
		"externalInitiatorName": ei.Name,
		"externalInitiatorURL":  ei.URL,
	})

	resp := presenters.NewExternalInitiatorAuthentication(*ei, *eia)
	jsonAPIResponseWithStatus(c, resp, "external initiator authentication", http.StatusCreated)
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

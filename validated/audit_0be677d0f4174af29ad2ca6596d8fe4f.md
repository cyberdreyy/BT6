### Title
Persistent disclosure of External Initiator `AccessKey`/`OutgoingToken` on every list/show call - ([File: core/web/presenters/external_initiators.go])

### Summary
`NewExternalInitiatorResource` (used by the Index/list handler) unconditionally serializes the External Initiator's live `AccessKey` and `OutgoingToken` in every response, with no create-vs-list distinction. This differs from the Bridge resource pattern, where only a hash (`IncomingTokenHash`) is persisted and the plaintext `IncomingToken` is returned solely at creation time via `BridgeTypeAuthentication`.

### Finding Description
`ExternalInitiatorsController.Index` calls `eic.App.BridgeORM().ExternalInitiators(...)` and, for every record returned, builds a `presenters.ExternalInitiatorResource` via `NewExternalInitiatorResource`, which copies `ei.AccessKey` and `ei.OutgoingToken` directly from the stored `bridges.ExternalInitiator` model into the JSON response fields `accessKey` and `outgoingToken` (no `omitempty`, no redaction): [1](#0-0) 

Compare this to `BridgeType`, which stores only `IncomingTokenHash` + `Salt` (no plaintext secret persisted at all), and whose presenter `BridgeResource` never includes the incoming token — the plaintext `IncomingToken` is only ever returned once, in `BridgeTypeAuthentication`, at creation time: [2](#0-1) [3](#0-2) 

For External Initiators, `AccessKey` and `OutgoingToken` are stored and returned in plaintext on both `Create` and every subsequent `Index` call: [4](#0-3) [5](#0-4) 

There is no redaction, hashing-at-rest, or "shown once" mechanism analogous to bridges. Any caller who can reach `GET /v2/external_initiators` therefore receives the live `accessKey`/`outgoingToken` for every EI in the list, repeatedly, for as long as the EI exists.

### Impact Explanation
This is a credential/secret disclosure issue: `AccessKey` is the credential used to authenticate inbound webhook requests to the node as that External Initiator, and `OutgoingToken` is used by the node's outbound EI notification calls. Repeated exposure of these live secrets to any caller reaching the Index endpoint allows request impersonation of the external initiator (forging job-triggering webhook calls) — matching Chainlink's "key/secret disclosure leading to unauthorized action" bounty impact class. Since these are not one-time-shown or hashed values, compromise is not limited to the creation transaction; every list call re-exposes them, and there is no way to detect after-the-fact reuse.

### Likelihood Explanation
Reaching this issue requires only the ability to make an authenticated GET request to `/v2/external_initiators` (per the question's precondition of "any authenticated caller reaching the Index route"). No special privilege beyond whatever session/role gates that route is needed, and unlike a one-time secret, the exposure is reproducible on every request — it does not depend on a race with initial creation. This is fully deterministic and repeatable by design, since the presenter always includes these fields.

### Recommendation
- Do not persist or return `AccessKey`/`OutgoingToken` as retrievable plaintext for list/show operations. Follow the bridge pattern: hash the access credential at rest (e.g., store `AccessKeyHash`/salt), and only return the plaintext value once, in the `Create` response via `ExternalInitiatorAuthentication`.
- Remove `AccessKey` and `OutgoingToken` from `ExternalInitiatorResource` (used by `Index`), or replace them with redacted/omitted values, mirroring `BridgeResource`'s create-only `IncomingToken` field.

### Proof of Concept
Go handler-level integration test plan (in `core/web/external_initiators_controller_test.go`):
1. Create an EI via `POST /v2/external_initiators`; capture the returned `accessKey`/`outgoingToken` from the create response.
2. Call `GET /v2/external_initiators` (Index) and assert the returned `ExternalInitiatorResource.AccessKey` and `.OutgoingToken` fields are empty/omitted/redacted, not equal to the values captured at creation — this assertion currently fails against `NewExternalInitiatorResource`.
3. Repeat the GET a second time to demonstrate persistent (not one-time) exposure, confirming the invariant "secret confinement — credentials should be shown once at creation only" is violated on each subsequent Index call.

### Citations

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

**File:** core/bridges/bridge_type.go (L44-68)
```go
// BridgeTypeAuthentication is the record returned in response to a request to create a BridgeType
type BridgeTypeAuthentication struct {
	Name                   BridgeName
	URL                    models.WebURL
	Confirmations          uint32
	IncomingToken          string
	OutgoingToken          string
	MinimumContractPayment *assets.Link
	UseConnectionManager   bool `json:"useConnectionManager"`
}

// BridgeType is used for external adapters and has fields for
// the name of the adapter and its URL.
type BridgeType struct {
	Name                   BridgeName
	URL                    models.WebURL
	Confirmations          uint32
	IncomingTokenHash      string
	Salt                   string
	OutgoingToken          string
	MinimumContractPayment *assets.Link
	CreatedAt              time.Time
	UpdatedAt              time.Time
	UseConnectionManager   bool `json:"useConnectionManager"`
}
```

**File:** core/web/presenters/bridges.go (L10-22)
```go
// BridgeResource represents a Bridge JSONAPI resource.
type BridgeResource struct {
	JAID
	Name          string `json:"name"`
	URL           string `json:"url"`
	Confirmations uint32 `json:"confirmations"`
	// The IncomingToken is only provided when creating a Bridge
	IncomingToken          string       `json:"incomingToken,omitempty"`
	OutgoingToken          string       `json:"outgoingToken"`
	MinimumContractPayment *assets.Link `json:"minimumContractPayment"`
	UseConnectionManager   bool         `json:"useConnectionManager"`
	CreatedAt              time.Time    `json:"createdAt"`
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

**File:** core/web/external_initiators_controller.go (L98-99)
```go
	resp := presenters.NewExternalInitiatorAuthentication(*ei, *eia)
	jsonAPIResponseWithStatus(c, resp, "external initiator authentication", http.StatusCreated)
```

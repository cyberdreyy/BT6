### Title
Plaintext OutgoingToken exposed to non-admin (view/run-role) sessions via BridgeResource presenter - ([File: core/web/presenters/bridges.go])

### Summary
`BridgeType.OutgoingToken` is stored and kept in plaintext (unlike `IncomingToken`, which is hashed via `incomingTokenHash` and never persisted). The `BridgeResource` presenter unconditionally serializes this plaintext `OutgoingToken` field with no role-based redaction, so any authenticated caller who can reach the bridge read routes (`GET /v2/bridge_types` / `GET /v2/bridge_types/:name`) receives the secret used to authenticate outbound requests from external adapters back to the node.

### Finding Description
`NewBridgeType` generates two secrets: `incomingToken` (hashed via `incomingTokenHash` and stored only as `IncomingTokenHash`+`Salt`) and `outgoingToken` (stored as plaintext `OutgoingToken` on the persisted `BridgeType`) [1](#0-0) . The `BridgeTypeAuthentication` struct returned once at creation time also carries the plaintext `IncomingToken`, but that value is never persisted to the DB — only its hash is [2](#0-1) .

The read-side presenter, `BridgeResource`, defines `OutgoingToken string \`json:"outgoingToken"\`` (no `omitempty`, no role gating), and `NewBridgeResource` copies `b.OutgoingToken` directly from the stored `bridges.BridgeType` into the response resource [3](#0-2) . This presenter is used by `BridgeTypesController` for both index and show responses, meaning any GET request against `/v2/bridge_types` or `/v2/bridge_types/:name` returns the plaintext `OutgoingToken` in the JSON body, regardless of whether the caller only has run/view privileges rather than admin/edit privileges needed to manage bridges.

### Impact Explanation
`OutgoingToken` is the credential a bridge/external adapter uses to authenticate callbacks back into the Chainlink node (e.g., to post job run results). Disclosure of this plaintext secret to a lower-privileged session lets that user impersonate the external adapter's outgoing callback channel toward the node, which is a secret/credential disclosure and potential job-run/data-integrity manipulation vector — falling under Chainlink's "sensitive data exposure" / "unauthorized data or secret disclosure" bounty impact class.

### Likelihood Explanation
Exploitation only requires any authenticated session capable of hitting the bridge read endpoints (view or run role, as stipulated in the audit precondition) and does not require admin/edit privileges. It is trivially repeatable: one GET request per bridge name returns the token every time, with no additional preconditions, timing, or race requirements.

### Recommendation
Remove or redact `OutgoingToken` from `BridgeResource` for non-admin/non-edit roles (mirroring how `IncomingToken` is only ever exposed once, at creation time, and never re-served). Concretely, drop `OutgoingToken` from the read-path presenter entirely (since it's a secret used only for outbound authentication verification and does not need to be re-displayed), or gate its inclusion behind an explicit admin-only presenter/route, and audit `core/web/bridge_types_controller.go`'s Index/Show handlers and router role assignments for `/v2/bridge_types*` to ensure secret fields are never returned to view/run-role callers.

### Proof of Concept
1. Add a Go handler-level test in `core/web/bridge_types_controller_test.go`:
   - Create an app with a run-role (or view-role) authenticated HTTP client.
   - Create a bridge via admin session (`POST /v2/bridge_types`), capturing the persisted `OutgoingToken`.
   - Using the run/view-role client, call `GET /v2/bridge_types/:name`.
   - Assert the JSON response body's `data.attributes.outgoingToken` field is empty/absent, and that it does not equal the value returned at creation time.
2. Add a presenter unit test in `core/web/presenters/bridges_test.go` asserting `NewBridgeResource(bridgeType)` does not populate `OutgoingToken` (or that the JSON-marshaled resource omits `outgoingToken`) for non-privileged contexts, matching how `IncomingToken` is never populated on read-back resources.

### Citations

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

**File:** core/bridges/bridge_type.go (L70-102)
```go
// NewBridgeType returns a bridge type authentication (with plaintext
// password) and a bridge type (with hashed password, for persisting)
func NewBridgeType(btr *BridgeTypeRequest) (*BridgeTypeAuthentication,
	*BridgeType, error,
) {
	incomingToken := utils.NewSecret(24)
	outgoingToken := utils.NewSecret(24)
	salt := utils.NewSecret(24)

	hash, err := incomingTokenHash(incomingToken, salt)
	if err != nil {
		return nil, nil, err
	}

	return &BridgeTypeAuthentication{
		Name:                   btr.Name,
		URL:                    btr.URL,
		Confirmations:          btr.Confirmations,
		IncomingToken:          incomingToken,
		OutgoingToken:          outgoingToken,
		MinimumContractPayment: btr.MinimumContractPayment,
		UseConnectionManager:   btr.UseConnectionManager,
	}, &BridgeType{
		Name:                   btr.Name,
		URL:                    btr.URL,
		Confirmations:          btr.Confirmations,
		IncomingTokenHash:      hash,
		Salt:                   salt,
		OutgoingToken:          outgoingToken,
		MinimumContractPayment: btr.MinimumContractPayment,
		UseConnectionManager:   btr.UseConnectionManager,
	}, nil
}
```

**File:** core/web/presenters/bridges.go (L10-41)
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

// GetName implements the api2go EntityNamer interface
func (r BridgeResource) GetName() string {
	return "bridges"
}

// NewBridgeResource constructs a new BridgeResource
func NewBridgeResource(b bridges.BridgeType) *BridgeResource {
	return &BridgeResource{
		// Uses the name as the id...Should change this to the id
		JAID:                   NewJAID(b.Name.String()),
		Name:                   b.Name.String(),
		URL:                    b.URL.String(),
		Confirmations:          b.Confirmations,
		OutgoingToken:          b.OutgoingToken,
		MinimumContractPayment: b.MinimumContractPayment,
		UseConnectionManager:   b.UseConnectionManager,
		CreatedAt:              b.CreatedAt,
	}
```

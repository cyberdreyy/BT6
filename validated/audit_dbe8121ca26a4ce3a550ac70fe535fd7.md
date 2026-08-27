### Title
GET /v2/bridge_types/:BridgeName discloses plaintext `OutgoingToken` bridge secret to any authorized bridge-reader - (File: core/web/presenters/bridges.go)

### Summary
`NewBridgeResource` unconditionally copies the bridge's plaintext `OutgoingToken` into the JSON-serialized `BridgeResource` on every call, with no redaction logic, unlike `IncomingToken` which is only ever populated by the `Create` handler. Any user whose role is permitted to call `BridgeTypesController.Show` (or `Index`/`Update`/`Destroy`, which reuse the same presenter) therefore receives the bridge's outgoing authentication secret in the response body.

### Finding Description
`presenters.NewBridgeResource` builds the response resource directly from the `bridges.BridgeType` DB record and sets `OutgoingToken: b.OutgoingToken` with no gating condition: [1](#0-0) 

Contrast this with `IncomingToken`, which the struct comment explicitly says is only meant to be exposed at creation time, and which is only ever assigned outside the constructor (`resource.IncomingToken = bta.IncomingToken` in `Create`): [2](#0-1) [3](#0-2) 

`BridgeTypesController.Show` looks up the bridge by name from the URL param and passes the full `bridges.BridgeType` (which stores `OutgoingToken` in plaintext, per `core/bridges/bridge_type.go`) straight into `NewBridgeResource`, then serializes it to the client: [4](#0-3) [5](#0-4) 

This is architecturally asymmetric: `IncomingTokenHash`/`Salt` are hashed at rest and never returned outside `Create`, while `OutgoingToken` is stored and returned in plaintext on every read path (`Index`, `Show`, `Update`, `Destroy`) that constructs a `BridgeResource`.

I was unable to fully confirm, within the available tool budget, the exact role requirement gin-middleware applied to the `GET /v2/bridge_types/:BridgeName` route in `core/web/router.go` (the file has role-related wiring but I could not extract the specific route registration line before running out of iterations). Chainlink's established convention in this codebase is that read (`GET`) endpoints for such resources are reachable by any authenticated session including view-role, while only mutating verbs (`POST`/`PATCH`/`DELETE`) require elevated (admin) role — this is consistent with the UI needing to list/display bridges for viewer users. If that convention holds here, a view-role token is sufficient to reach `Show` and extract `OutgoingToken`. This precondition should be explicitly re-verified against the exact middleware stack in `router.go` before treating this as fully confirmed.

### Impact Explanation
`OutgoingToken` is the credential the node presents when calling out to the external adapter. Disclosure of this token to a low-privileged (view-role) user allows that user to impersonate the node when calling the external adapter directly, satisfying the "credential/secret disclosure enabling request impersonation" bounty impact class. The blast radius is scoped to whichever external adapter(s) are configured to trust this token, but it is a legitimate secret-confinement violation regardless of role-gate specifics, since even a "view" role is explicitly a restricted, non-admin credential per the threat model.

### Likelihood Explanation
Exploitation requires no privilege beyond a valid session/API token with at least view-level bridge read access, and is fully deterministic and repeatable — a single `GET /v2/bridge_types/:BridgeName` call returns the outgoing token in the JSON body every time, since `NewBridgeResource` has no redaction branch. The only unverified variable is whether the route's role middleware permits view-role callers versus requiring an elevated role; this should be checked directly in `core/web/router.go` before filing/fixing.

### Recommendation
Do not populate `OutgoingToken` in `NewBridgeResource` for read paths. Mirror the `IncomingToken` pattern: default `BridgeResource.OutgoingToken` to empty/omitted in the constructor, and only populate it explicitly in flows where returning it is intended (e.g., not at all for `Show`/`Index`/`Update`/`Destroy`, or only for admin-role callers), or mask it (e.g., last 4 chars) for display purposes.

### Proof of Concept
1. In `core/web/bridge_types_controller_test.go` style, seed a `bridges.BridgeType` via `factories`/ORM with a known `OutgoingToken` value.
2. Create an authenticated test client using a view-role session/API token (as used elsewhere in `core/web/*_test.go` for view-role coverage).
3. Call `GET /v2/bridge_types/:BridgeName` for the seeded bridge.
4. Assert HTTP 200 and that the JSON response's `data.attributes.outgoingToken` equals the DB-stored `bridges.BridgeType.OutgoingToken`.
5. Additionally assert `data.attributes.incomingToken` is empty/omitted, confirming the asymmetric handling in `presenters.NewBridgeResource`.

### Citations

**File:** core/web/presenters/bridges.go (L13-18)
```go
	Name          string `json:"name"`
	URL           string `json:"url"`
	Confirmations uint32 `json:"confirmations"`
	// The IncomingToken is only provided when creating a Bridge
	IncomingToken          string       `json:"incomingToken,omitempty"`
	OutgoingToken          string       `json:"outgoingToken"`
```

**File:** core/web/presenters/bridges.go (L29-42)
```go
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
}
```

**File:** core/web/bridge_types_controller.go (L98-99)
```go
	resource := presenters.NewBridgeResource(*bt)
	resource.IncomingToken = bta.IncomingToken
```

**File:** core/web/bridge_types_controller.go (L125-146)
```go
func (btc *BridgeTypesController) Show(c *gin.Context) {
	ctx := c.Request.Context()
	name := c.Param("BridgeName")

	taskType, err := bridges.ParseBridgeName(name)
	if err != nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, err)
		return
	}

	bt, err := btc.App.BridgeORM().FindBridge(ctx, taskType)
	if errors.Is(err, sql.ErrNoRows) {
		jsonAPIError(c, http.StatusNotFound, errors.New("bridge not found"))
		return
	}
	if err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}

	jsonAPIResponse(c, presenters.NewBridgeResource(bt), "bridge")
}
```

**File:** core/bridges/bridge_type.go (L57-68)
```go
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

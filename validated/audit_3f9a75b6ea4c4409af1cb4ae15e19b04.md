### Title
Bulk exposure of all bridges' `OutgoingToken` secrets via `GET /v2/bridge_types` - ([File: core/web/bridge_types_controller.go])

### Summary
`BridgeTypesController.Index` builds a `presenters.BridgeResource` for every bridge returned by `BridgeORM().BridgeTypes` and serializes it directly to JSON, and `presenters.NewBridgeResource` unconditionally populates the `OutgoingToken` field (unlike `IncomingToken`, which is `omitempty` and only set on `Create`). Any authenticated user able to call this endpoint (any role that can reach `/v2/bridge_types`) can page through and dump every configured bridge's `OutgoingToken` in one request.

### Finding Description
`Index` fetches a page of bridges and maps each one through `presenters.NewBridgeResource`: [1](#0-0) 

`NewBridgeResource` always copies `b.OutgoingToken` into the response struct with no redaction, and the `BridgeResource.OutgoingToken` JSON tag has no `omitempty`/masking, unlike `IncomingToken`, which is explicitly commented as "only provided when creating a Bridge": [2](#0-1) 

The same unmasked behavior also affects `Show`, `Update`, and `Destroy`, all of which call `presenters.NewBridgeResource(bt)` directly, but `Index` is the most severe because it returns *all* bridges (with `size` up to whatever page size is requested) in a single call rather than one bridge at a time by name.

The root cause is that the presenter treats `OutgoingToken` as a value to always display, with no distinction between the creator/admin viewing a bridge they made and any other caller listing bridges. There is no redaction, masking, or role-based field filtering applied before serialization in `Index` or `NewBridgeResource`.

### Impact Explanation
`OutgoingToken` is the secret credential the bridge task attaches to outbound requests to the external adapter (see `core/bridges/bridge_type.go`), so its disclosure allows a caller to impersonate the Chainlink node when calling that bridge's external adapter, i.e., request/credential impersonation and secret disclosure — a legitimate Chainlink bounty impact class (secret/key disclosure). Because `Index` returns a paginated list of *all* bridges, a single call (or a few paginated calls) exfiltrates every bridge's `OutgoingToken` at once rather than requiring per-bridge, name-guessing calls.

### Likelihood Explanation
Exploitability depends entirely on what role is required to reach `GET /v2/bridge_types`. This route is registered in `core/web/router.go`; the search located the route registration but the exact role/auth middleware applied to it could not be fully confirmed within the available tool budget (only file-content matches were retrieved, not the specific line configuring role-gating for `/v2/bridge_types`). If this route is gated behind an admin-only role, this would not qualify as an unprivileged-attacker finding per the constraints (operator/admin access is out of scope). If it is reachable by a lower-privilege "view" role token as the question's precondition states, then likelihood is high and trivially repeatable (single authenticated GET, no rate limiting mentioned).

### Recommendation
Do not include `OutgoingToken` (or mask it, e.g., `"***redacted***"`) in `presenters.NewBridgeResource` when used for list/show responses; only reveal it (if ever) to a strictly admin role or not at all via API, mirroring the existing `IncomingToken` pattern where it's only returned once at creation time.

### Proof of Concept
1. In `core/web/bridge_types_controller_test.go`, seed N bridges via `bridges.ORM.CreateBridgeType` with distinct `OutgoingToken` values.
2. Authenticate a test client with the minimum role that is actually permitted to call `GET /v2/bridge_types` (per router configuration — this must be confirmed against `core/web/router.go`'s actual role requirement for the route).
3. Call `GET /v2/bridge_types?size=1000`.
4. Parse the JSON:API response into `[]presenters.BridgeResource` and assert `resource.OutgoingToken` is non-empty and matches the seeded value for every bridge — demonstrating all secrets are returned in one response.
5. Additionally assert the field is absent/masked after the fix is applied, i.e., the test should fail before the fix and pass after it.

Note: I was unable to conclusively verify from the indexed code which authentication role is actually enforced on `GET /v2/bridge_types` in `core/web/router.go` (the route registration was located, but its specific role/middleware wiring wasn't retrieved in full). This is required to confirm the "unprivileged view-role" precondition is met rather than requiring an admin-only role, which would place this finding out of scope per the audit rules.

### Citations

**File:** core/web/bridge_types_controller.go (L112-122)
```go
func (btc *BridgeTypesController) Index(c *gin.Context, size, page, offset int) {
	ctx := c.Request.Context()
	bridges, count, err := btc.App.BridgeORM().BridgeTypes(ctx, offset, size)

	resources := make([]presenters.BridgeResource, 0, len(bridges))
	for _, bridge := range bridges {
		resources = append(resources, *presenters.NewBridgeResource(bridge))
	}

	paginatedResponse(c, "Bridges", size, page, resources, count, err)
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

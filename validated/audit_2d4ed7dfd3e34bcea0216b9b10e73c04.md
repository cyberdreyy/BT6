### Title
BridgeTypesController.Show discloses bridge OutgoingToken to view-role users via unmasked presenter - ([File: core/web/router.go])

### Summary
`BridgeTypesController.Show` (`core/web/bridge_types_controller.go`) returns a `presenters.BridgeResource` built from the raw `bridges.BridgeType` without any redaction of `OutgoingToken`. Unlike `Create`, `Update`, and `Destroy`, `Show` is exposed at a route that only requires view-level session/authentication, not editor role, causing plaintext bridge credentials to be returned to lower-privileged callers.

### Finding Description
`BridgeTypesController.Show` looks up the bridge by name and serializes it via `presenters.NewBridgeResource(bt)`: [1](#0-0) 

`presenters.BridgeResource` marshals `OutgoingToken` with a plain JSON tag (`json:"outgoingToken"`, no `omitempty`, no masking), and `NewBridgeResource` copies `b.OutgoingToken` directly from the DB-backed `bridges.BridgeType` struct into the resource unconditionally: [2](#0-1) 

Only `IncomingToken` is intentionally special-cased (commented "only provided when creating a Bridge") and left empty by `Show`/`Update`/`Destroy` since those handlers never assign `resource.IncomingToken`. No such care is taken for `OutgoingToken` — it is populated from the stored value in every code path that constructs a `BridgeResource`, including `Show`, `Index`, `Update`, and `Destroy`.

Per the question's stated routing configuration, `Create`/`Update`/`Destroy` are gated behind an editor-role wrapper in `core/web/router.go`, while the `Show` route (`GET /v2/bridge_types/:BridgeName`) is registered without that wrapper — requiring only a valid authenticated/view-role session. This means any caller with view-only credentials (a `view` role API token, or in general the lowest privilege authenticated principal) can call `GET /v2/bridge_types/<name>` and receive the bridge's `OutgoingToken` in the JSON response body, even though write operations on that same secret are edit-gated.

### Impact Explanation
`OutgoingToken` is the credential used by the Chainlink node to authenticate itself with the external adapter/bridge endpoint. Its disclosure to a view-role principal breaks secret confinement: a lesser-privileged, non-editor identity can extract EA-facing credentials that should only ever be visible/settable by edit/admin roles. This matches the "job spec contents including bridge credentials" disclosure impact class in the bounty scope.

### Likelihood Explanation
The precondition is minimal: any authenticated session or API token with only view role (or any principal that can reach the `GET /v2/bridge_types/:BridgeName` route) suffices — no editor/admin privilege is required. The exploit is a single unauthenticated-to-role GET request, fully repeatable for any bridge with a non-empty `OutgoingToken`, making this straightforward and reliable to reproduce.

### Recommendation
Mask/omit `OutgoingToken` in `presenters.BridgeResource` for read paths (`Show`, `Index`) the same way `IncomingToken` is already treated — e.g., only populate it when the caller has edit/admin role, or omit it entirely from `NewBridgeResource` and instead surface it only through a dedicated, editor-gated code path (as is done today for `IncomingToken` in `Create`). Additionally verify in `core/web/router.go` that `GET /v2/bridge_types/:BridgeName` requires at least the same role as mutation endpoints if the secret cannot be safely redacted.

### Proof of Concept
1. In `core/web/presenters/bridges_test.go`, add a table test asserting that `NewBridgeResource` output for a `bridges.BridgeType` with a non-empty `OutgoingToken` never surfaces that value (i.e., the field is empty/masked) when constructed for a "read" context.
2. Add a handler-level integration test in `core/web/bridge_types_controller_test.go`:
   - Seed a bridge with `OutgoingToken = "secret-token"`.
   - Issue `GET /v2/bridge_types/<name>` using a client authenticated with `view`-role credentials only (no editor role).
   - Assert HTTP 200 and that the decoded `BridgeResource.OutgoingToken` equals `"secret-token"`, demonstrating the leak.
   - Expected fix outcome: after remediation, the same request should return an empty/masked `OutgoingToken`.

### Citations

**File:** core/web/bridge_types_controller.go (L124-146)
```go
// Show returns the details of a specific Bridge.
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

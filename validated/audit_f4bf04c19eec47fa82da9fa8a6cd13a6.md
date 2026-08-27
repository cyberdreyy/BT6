### Title
Bridge outgoing auth token disclosed to any authenticated view-role user via GET /v2/bridge_types/:BridgeName - ([File: core/web/bridge_types_controller.go])

### Summary
`BridgeTypesController.Show` builds its response with `presenters.NewBridgeResource(bt)`, which unconditionally copies `bt.OutgoingToken` into `BridgeResource.OutgoingToken`. Unlike `IncomingToken`, this field has no `omitempty` and no role-based redaction, so any authenticated caller permitted to hit this read endpoint (including a view-role API token) receives the bridge's outgoing secret in plaintext.

### Finding Description
`presenters.BridgeResource` defines: [1](#0-0) 
The comment explicitly states `IncomingToken` is "only provided when creating a Bridge" and it does carry `omitempty`, but `OutgoingToken` has no such guard and is unconditionally set by `NewBridgeResource`: [2](#0-1) 

`BridgeTypesController.Show` looks up the bridge by name from the URL param and serializes it directly with `NewBridgeResource`, with no field stripping or role check beyond whatever generic authentication middleware wraps the route: [3](#0-2) 

The same unredacted presenter is also used by `Index`, `Update`, and `Destroy`, so the token leaks on every read of bridge metadata, not just `Show`. [4](#0-3) 

The `OutgoingToken` is a real secret: it is generated alongside the salted/hashed incoming token in `bridges.NewBridgeType` and persisted in `BridgeType.OutgoingToken`, intended to let the node authenticate itself to the external adapter (as opposed to `IncomingTokenHash`, which authenticates inbound calls from the adapter to the node): [5](#0-4) [6](#0-5) 

Because `Show` (and `Index`/`Update`/`Destroy`) is a read/query-style action reachable by any credential that can authenticate to the node's `/v2/bridge_types` routes, and the secret-bearing field lacks `omitempty`/redaction, the outgoing token is returned in the JSON:API response body to whoever can invoke it — this is a straightforward secret-confinement violation independent of whether the caller has edit/admin rights on bridges.

### Impact Explanation
Disclosure of `OutgoingToken` lets an attacker who obtains it impersonate the Chainlink node when calling out to the external adapter (if the adapter validates this token on inbound calls), or otherwise use it to forge/replay responses purporting to come from the node, undermining the integrity of bridge-based job results. This falls under Chainlink's "sensitive data / credential exposure" impact class rather than a full key compromise, but it is a concrete secret leak enabling downstream job-response forgery.

### Likelihood Explanation
The only precondition is possessing any credential able to authenticate against `/v2/bridge_types/:BridgeName` and knowing (or guessing) a bridge name; bridge names are often predictable/enumerable via job specs. The bug is deterministic and repeatable on every call — there is no rate limiting or one-time exposure semantics as exists for `IncomingToken` at creation time. The main uncertainty is the exact role gate wired up in `core/web/router.go` for this route (whether a "view"-only role can reach `Show`); I was not able to fully confirm the router's role-to-route mapping within the available context, so the "view-role" precondition should be verified against the router's route table before treating this as reachable by the lowest-privilege authenticated role. Regardless of exact role required, the presenter itself has no redaction, so any role permitted to call these GET endpoints obtains the secret.

### Recommendation
Add `omitempty` to `BridgeResource.OutgoingToken` and stop populating it in `NewBridgeResource` for read paths (`Show`, `Index`), mirroring how `IncomingToken` is only attached in `Create`. If `OutgoingToken` must ever be surfaced again, restrict it to bridge-creation/rotation flows and to callers with an explicit "edit"/"admin" role, never on plain reads.

### Proof of Concept
Handler-level integration test plan (Go):
1. In `core/web/bridge_types_controller_test.go`, create a bridge via `Create` to obtain `bt.OutgoingToken` value stored in the DB.
2. Issue `GET /v2/bridge_types/<name>` using a session/API token scoped to the lowest read-capable role available in the router configuration.
3. Decode the JSON:API response into `presenters.BridgeResource` and assert `resource.OutgoingToken == ""` (expected to fail against current code, proving the secret is returned).
4. Repeat for `Index` and `Update`/`Destroy` responses to confirm the same leak across all bridge read/write endpoints that reuse `NewBridgeResource`.

### Citations

**File:** core/web/presenters/bridges.go (L16-18)
```go
	// The IncomingToken is only provided when creating a Bridge
	IncomingToken          string       `json:"incomingToken,omitempty"`
	OutgoingToken          string       `json:"outgoingToken"`
```

**File:** core/web/presenters/bridges.go (L30-41)
```go
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

**File:** core/bridges/bridge_type.go (L55-68)
```go
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

**File:** core/bridges/bridge_type.go (L70-101)
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
```

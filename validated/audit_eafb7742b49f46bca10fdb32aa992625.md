### Title
GraphQL `CreateBridge` bypasses `ParseBridgeName` lowercasing, allowing case-variant bridge shadowing/hijack - ([File: core/web/resolver/mutation.go])

### Summary
The GraphQL `CreateBridge` and `UpdateBridge` resolvers construct `bridges.BridgeTypeRequest.Name` via a raw type cast `bridges.BridgeName(args.Input.Name)` instead of calling `bridges.ParseBridgeName`, so the mixed-case name is persisted and used for uniqueness checks as-is. Since `ValidateBridgeTypeUniqueness`/`FindBridge` use exact SQL equality (`WHERE name = $1`), an edit-role user can register a second bridge whose name differs only in case from an existing bridge, and job pipeline bridge lookups (which always lower-case via `ParseBridgeName`) will resolve to the attacker's newly-created bridge instead of the original.

### Finding Description
In `Resolver.CreateBridge` [1](#0-0) , the bridge name is set as `bridges.BridgeName(args.Input.Name)` — a plain string cast, not `bridges.ParseBridgeName(args.Input.Name)`. `ParseBridgeName` is the function that both validates allowed characters and lower-cases the name: `return BridgeName(strings.ToLower(val)), nil` [2](#0-1) .

`ValidateBridgeType` only calls `ParseBridgeName` to check for a format error but discards the normalized result (`if _, err := bridges.ParseBridgeName(bt.Name.String()); err != nil`), so `btr.Name` keeps its original case [3](#0-2) . `ValidateBridgeTypeUniqueness` then looks up the bridge by the un-normalized name via `orm.FindBridge(ctx, bt.Name)` [4](#0-3) , and `FindBridge` performs a case-sensitive SQL comparison: `SELECT * FROM bridge_types WHERE name = $1` [5](#0-4) . `NewBridgeType` copies `btr.Name` verbatim into the persisted `BridgeType.Name` without any normalization [6](#0-5) .

As a result:
1. `createBridge(name: "MyBridge")` succeeds and persists `name = "MyBridge"`.
2. `createBridge(name: "mybridge")` — the uniqueness check performs `WHERE name = 'mybridge'`, which does not match the stored `"MyBridge"` row, so the check passes and a second, independent bridge row `name = "mybridge"` is created.

The `UpdateBridge` resolver has the same un-normalized `Name` construction, though its `FindBridge` lookup for update is keyed off `bridges.ParseBridgeName(string(args.ID))` (the bridge ID), so the primary exploitable path is `CreateBridge`.

The impact: job pipeline `BridgeTask` lookups always normalize via `ParseBridgeName` before querying, e.g. `AssertBridgesExist` calls `bridges.ParseBridgeName(name)` on the task's bridge name [7](#0-6) , and `getBridgeFromName` calls `t.orm.FindBridge(ctx, bridges.BridgeName(name))` where `name` originates from the DOT spec/StringParam and is generally lower-cased by prior parsing [8](#0-7) . Because these runtime lookups use the lower-cased form, if an attacker registers `"mybridge"` after a legitimate `"MyBridge"` already exists, any job task referencing the bridge by lower-case name (the common/normalized form) would resolve against the attacker's row — the attacker's URL/token, not the original bridge owner's — while the REST/jsonapi bridge creation path (`bridge_types_controller.go`, which relies on `BridgeTypeRequest.SetID` → `ParseBridgeName`) always normalizes and would not itself allow the collision.

### Impact Explanation
This allows a bridge-hijack style redirection of DON HTTP calls: an edit-role user could pre-register a case-variant of an existing bridge name and have job pipeline executions actually invoke the attacker-controlled bridge URL/token instead of the intended one, since pipeline bridge task resolution normalizes to lower-case before lookup. This matches the "unauthorized action on another user's job" / cross-user response confusion impact class, scoped only to the GraphQL bridge mutation path (`CreateBridge`).

### Likelihood Explanation
Requires only an edit-role authenticated session (already an "editor" of the node's bridges/jobs configuration, not a low-privilege attacker under the excluded classes) and two straightforward GraphQL mutation calls — highly feasible and repeatable given no additional preconditions or timing constraints. Note: because the precondition is possessing an edit-role credential (a legitimate, if lower-privileged, application user), the severity depends on the trust model between different edit-role users sharing a single node, which is a realistic multi-operator node scenario.

### Recommendation
In `Resolver.CreateBridge` and `Resolver.UpdateBridge` (core/web/resolver/mutation.go), replace the raw cast `bridges.BridgeName(args.Input.Name)` with `bridges.ParseBridgeName(args.Input.Name)` and propagate/handle the returned error, matching the behavior of `BridgeTypeRequest.SetID` used by the REST controller. Additionally, consider making `ValidateBridgeTypeUniqueness`/`FindBridge` explicitly case-insensitive (e.g., `WHERE lower(name) = lower($1)`, mirroring `FindExternalInitiatorByName`) as defense in depth, and/or add a case-insensitive unique index on `bridge_types.name`.

### Proof of Concept
Go handler-level integration test plan:
1. Build a test GraphQL app/resolver context authenticated as an edit-role user (as used in existing resolver tests, e.g. `core/web/resolver/bridge_test.go` patterns).
2. Call `CreateBridge` mutation with `Input.Name = "MyBridge"`, `URL` valid; assert success and that `orm.FindBridge(ctx, "MyBridge")` returns the created row with `Name == "MyBridge"` (exact case, unnormalized) — demonstrating `ParseBridgeName` was bypassed.
3. Call `CreateBridge` mutation again with `Input.Name = "mybridge"`; assert (bug) that this call **succeeds** rather than returning "bridge type already exists", and that `orm.FindBridge(ctx, "mybridge")` returns a *second*, distinct row with a different `IncomingTokenHash`/`Salt` from the first.
4. Add a unit test on `ValidateBridgeTypeUniqueness` in `core/web/resolver/helpers_test.go` directly: create a bridge named `"MyBridge"` via `orm.CreateBridgeType`, then call `ValidateBridgeTypeUniqueness(ctx, &bridges.BridgeTypeRequest{Name: "mybridge"}, orm)` and assert it currently returns `nil` (no conflict) — confirming the case-insensitive collision is not caught.
5. (Fix verification) After applying the recommended `ParseBridgeName` normalization, re-run steps 2–4 and assert the second `CreateBridge` call now fails with "bridge type already exists".

### Citations

**File:** core/web/resolver/mutation.go (L81-87)
```go
	btr := &bridges.BridgeTypeRequest{
		Name:                   bridges.BridgeName(args.Input.Name),
		URL:                    webURL,
		Confirmations:          uint32(max(0, args.Input.Confirmations)),
		MinimumContractPayment: minContractPayment,
		UseConnectionManager:   args.Input.UseConnectionManager != nil && *args.Input.UseConnectionManager,
	}
```

**File:** core/bridges/bridge_type.go (L84-101)
```go
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

**File:** core/bridges/bridge_type.go (L154-161)
```go
// ParseBridgeName returns a formatted Task type.
func ParseBridgeName(val string) (BridgeName, error) {
	if !bridgeNameRegex.MatchString(val) {
		return "", fmt.Errorf("task type validation: name %v contains invalid characters", val)
	}

	return BridgeName(strings.ToLower(val)), nil
}
```

**File:** core/web/resolver/helpers.go (L56-66)
```go
func ValidateBridgeTypeUniqueness(ctx context.Context, bt *bridges.BridgeTypeRequest, orm bridges.ORM) error {
	_, err := orm.FindBridge(ctx, bt.Name)
	if err == nil {
		return fmt.Errorf("bridge type %v already exists", bt.Name)
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return fmt.Errorf("error determining if bridge type %v already exists", bt.Name)
	}

	return nil
}
```

**File:** core/web/resolver/helpers.go (L73-79)
```go
func ValidateBridgeType(bt *bridges.BridgeTypeRequest) error {
	if len(bt.Name.String()) < 1 {
		return errors.New("No name specified")
	}
	if _, err := bridges.ParseBridgeName(bt.Name.String()); err != nil {
		return errors.Wrap(err, "invalid bridge name")
	}
```

**File:** core/bridges/orm.go (L58-63)
```go
func (o *orm) FindBridge(ctx context.Context, name BridgeName) (bt BridgeType, err error) {
	stmt := "SELECT * FROM bridge_types WHERE name = $1"
	err = o.ds.GetContext(ctx, &bt, stmt, name.String())

	return
}
```

**File:** core/services/job/orm.go (L140-157)
```go
func (o *orm) AssertBridgesExist(ctx context.Context, p pipeline.Pipeline) error {
	var bridgeNames = make(map[bridges.BridgeName]struct{})
	var uniqueBridges []bridges.BridgeName
	for _, task := range p.Tasks {
		if task.Type() == pipeline.TaskTypeBridge {
			// Bridge must exist
			name := task.(*pipeline.BridgeTask).Name
			bridge, err := bridges.ParseBridgeName(name)
			if err != nil {
				return err
			}
			if _, have := bridgeNames[bridge]; have {
				continue
			}
			bridgeNames[bridge] = struct{}{}
			uniqueBridges = append(uniqueBridges, bridge)
		}
	}
```

**File:** core/services/pipeline/task.bridge.go (L470-476)
```go
func (t *BridgeTask) getBridgeFromName(ctx context.Context, name StringParam) (bridges.BridgeType, error) {
	bt, err := t.orm.FindBridge(ctx, bridges.BridgeName(name))
	if err != nil {
		return bridges.BridgeType{}, errors.Wrapf(err, "could not find bridge with name '%s'", name)
	}
	return bt, nil
}
```

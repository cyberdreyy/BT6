### Title
CreateBridge GraphQL mutation stores bridge names with original case instead of normalized lowercase, allowing case-variant bridge name collisions to bypass uniqueness checks - ([File: core/web/resolver/mutation.go])

### Summary
The GraphQL `Resolver.CreateBridge` mutation constructs `btr.Name` directly from `bridges.BridgeName(args.Input.Name)` without routing it through `bridges.ParseBridgeName`, which is the function responsible for lowercasing bridge names. Because the normalized name returned by `ValidateBridgeType`'s internal call to `ParseBridgeName` is discarded, the bridge is persisted to the database with the caller-supplied case, and the database-level lookup (`FindBridge`) performs a case-sensitive `=` comparison, so `MyBridge` and `mybridge` are treated as distinct bridges.

### Finding Description
In `core/web/resolver/mutation.go`, `CreateBridge` builds the request as: [1](#0-0) 
Note `Name: bridges.BridgeName(args.Input.Name)` is a raw type cast — it does not call `bridges.ParseBridgeName`, which lowercases via `strings.ToLower(val)`: [2](#0-1) 

The subsequent validation, `ValidateBridgeType` in `core/web/resolver/helpers.go`, calls `ParseBridgeName` only to check for an error, discarding the normalized (lowercased) return value: [3](#0-2) 

So `btr.Name` retains its original case for the remainder of the flow: `bridges.NewBridgeType(btr)` copies `btr.Name` as-is into both the `BridgeTypeAuthentication` and `BridgeType` structs without normalization: [4](#0-3) 

`ValidateBridgeTypeUniqueness` then looks up the bridge via `orm.FindBridge(ctx, bt.Name)`: [5](#0-4) 

And `FindBridge` in `core/bridges/orm.go` performs a case-sensitive SQL equality lookup: [6](#0-5) 

Because `name = $1` in PostgreSQL is case-sensitive by default (no `LOWER()` normalization or `citext` column type evident in the queried code), a bridge `mybridge` already existing does not collide with a create request for `MyBridge`, and `orm.CreateBridgeType` will insert a second row with the different-case name.

Compare this to the REST controller `core/web/bridge_types_controller.go`, whose `Show` handler explicitly calls `bridges.ParseBridgeName(name)` before lookup to normalize case: [7](#0-6) 
But note the REST `Create` handler has the same underlying weakness — `ValidateBridgeType` there also discards the normalized name from `ParseBridgeName`, only checking the error: [8](#0-7) 

This means the case-collision issue is not unique to the GraphQL mutation but is a systemic gap across `core/web/resolver/helpers.go` and `core/web/bridge_types_controller.go`: `ParseBridgeName`'s normalization exists but is never actually applied to the name used for storage/uniqueness checks in bridge creation paths.

### Impact Explanation
An edit-role user can create a bridge whose name differs only in case from an existing bridge referenced by another user's job pipeline (e.g., a `bridge` task referencing `mybridge` in its DOT spec). Because job pipeline bridge-task resolution also goes through `bridges.ParseBridgeName` (see `core/services/job/orm.go`'s `AssertBridgesExist`, which lowercases task names before lookup), whether this leads to actual job hijacking depends on whether the job's task name string was stored/queried with the same normalization as the pipeline task execution path — that code path lowercases consistently via `ParseBridgeName`, so a bridge task always resolves to the lowercase-normalized name at job-run time, not the differently-cased row created via the exploit. This limits practical impact to: (1) confusing/duplicate bridge type listings, (2) inability of the attacker's case-variant bridge to actually intercept another job's requests, since job execution normalizes to lowercase and would still hit the original bridge. The bounty-relevant impact is therefore closer to a data integrity/duplicate-entry issue rather than a confirmed cross-user job-hijack, because the exploitation chain to actually redirect job traffic requires the job-run path to also skip normalization, which was not found in the reviewed code.

### Likelihood Explanation
Any edit-role authenticated GraphQL user can trivially trigger this by calling `createBridge` with a name that differs in case from an existing bridge name. It requires only `authenticateUserCanEdit` privilege (edit role), no special preconditions, and is fully repeatable.

### Recommendation
Normalize `args.Input.Name` (and the corresponding REST controller input) through `bridges.ParseBridgeName` and use the returned normalized `BridgeName` when constructing `btr.Name`, instead of casting the raw user input directly. Apply the same fix in `ValidateBridgeType` (both in `core/web/resolver/helpers.go` and `core/web/bridge_types_controller.go`) to assign the normalized name back to `bt.Name`, ensuring uniqueness checks and persisted values are always case-normalized.

### Proof of Concept
```go
func Test_CreateBridge_CaseCollision(t *testing.T) {
    // 1. Create bridge "mybridge" via orm.CreateBridgeType directly (simulating existing bridge).
    // 2. Call Resolver.CreateBridge with args.Input.Name = "MyBridge".
    // 3. Assert that ValidateBridgeTypeUniqueness / orm.FindBridge treats "MyBridge" as colliding
    //    with "mybridge" (expected: error "bridge type MyBridge already exists"),
    //    but current code returns nil error and a second row is inserted with name="MyBridge".
    // 4. Query bridge_types table and assert only one row should exist for logically same name,
    //    demonstrating the current unique-name invariant is violated across case variants.
}
```

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

**File:** core/bridges/orm.go (L56-63)
```go
// FindBridge looks up a Bridge by its Name.
// Returns sql.ErrNoRows if name not present
func (o *orm) FindBridge(ctx context.Context, name BridgeName) (bt BridgeType, err error) {
	stmt := "SELECT * FROM bridge_types WHERE name = $1"
	err = o.ds.GetContext(ctx, &bt, stmt, name.String())

	return
}
```

**File:** core/web/bridge_types_controller.go (L35-53)
```go
// ValidateBridgeType checks that the bridge type has the required field with valid values.
func ValidateBridgeType(bt *bridges.BridgeTypeRequest) error {
	fe := models.NewJSONAPIErrors()
	if len(bt.Name.String()) < 1 {
		fe.Add("No name specified")
	}
	if _, err := bridges.ParseBridgeName(bt.Name.String()); err != nil {
		fe.Merge(err)
	}
	u := bt.URL.String()
	if len(strings.TrimSpace(u)) == 0 {
		fe.Add("URL must be present")
	}
	if bt.MinimumContractPayment != nil &&
		bt.MinimumContractPayment.Cmp(assets.NewLinkFromJuels(0)) < 0 {
		fe.Add("MinimumContractPayment must be positive")
	}
	return fe.CoerceEmptyToNil()
}
```

**File:** core/web/bridge_types_controller.go (L125-135)
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
```

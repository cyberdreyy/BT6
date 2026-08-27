### Title
Case-normalization after validation allows external initiator name collision bypass in `Create` - (File: core/web/external_initiators_controller.go)

### Summary
In `ExternalInitiatorsController.Create`, the incoming `bridges.ExternalInitiatorRequest.Name` is validated in its original, request-provided case by `ValidateExternalInitiator`, but the `ExternalInitiator` object that is actually persisted is built earlier by `bridges.NewExternalInitiator`, which stores `strings.ToLower(eir.Name)`. The uniqueness check and validated bytes therefore do not match the value that ends up in the database.

### Finding Description
`Create` first calls `bridges.NewExternalInitiator(eia, eir)` at [1](#0-0) , which lower-cases the name when constructing the object to persist: [2](#0-1) . Only afterward does it call `ValidateExternalInitiator(ctx, eir, eic.App.BridgeORM())` at [3](#0-2) , which validates `eir.Name` — the original, non-lowercased string — for format and for a pre-existing duplicate via `orm.FindExternalInitiatorByName(ctx, exi.Name)`: [4](#0-3) .

Because the duplicate-name lookup is performed against the case as submitted (e.g. `"FOO"`), while the value actually written to the database is `strings.ToLower(eir.Name)` (e.g. `"foo"`), an attacker can submit a name that differs only in case from an existing external initiator (e.g. `"foo"` already exists, attacker submits `"FOO"`). The uniqueness check against `"FOO"` finds no existing row and passes, but the object that is then persisted via `eic.App.BridgeORM().CreateExternalInitiator(ctx, ei)` is `ei.Name == "foo"`, colliding with the existing entry (subject to the DB's uniqueness constraint enforcement/collation behavior). This is a “validate one representation, persist another” pattern: the bytes checked by `ValidateExternalInitiator` (`eir.Name`, original case) are not the same as the bytes written to storage (`ei.Name`, lower-cased), violating the stated invariant that “the validated bytes and the executed object must be the same value.”

### Impact Explanation
This allows an authenticated non-admin ('edit' role) user to bypass the intended name-uniqueness invariant for external initiators by exploiting the case-folding mismatch between validation and persistence. Depending on downstream lookup behavior (`FindExternalInitiatorByName`) and DB collation, this can result in ambiguous or attacker-controlled external initiator identity resolution, since external initiators are used to authenticate remote job-run triggers. If a duplicate/collided name resolves unpredictably (e.g., last-write-wins or an unintended row match) during initiator authentication or webhook triggering, this could lead to attacker-influenced job runs — consistent with the reported "misreporting of prices and/or data: attacker-controlled oracle job input/output" impact class, though the exact blast radius depends on the SQL collation/uniqueness constraint of the `Name` column, which was not directly confirmed in this pass.

### Likelihood Explanation
Requires only an authenticated user with the 'edit' role able to hit `POST /v2/external_initiators` (no admin/operator privilege needed), and a pre-existing external initiator name to target for a case-variant collision. The bug is deterministic and repeatable: any two names differing only by case will trigger the validate/persist mismatch every time.

### Recommendation
Normalize the name (e.g., lower-case) once, before both validation and persistence — validate and persist the exact same string. E.g., change `Create` to lower-case `eir.Name` immediately after binding, then call both `ValidateExternalInitiator` and `bridges.NewExternalInitiator` with the already-normalized value, and ensure `FindExternalInitiatorByName` uniqueness checks are performed against that same normalized value (and ideally enforced at the DB level with a case-insensitive unique constraint).

### Proof of Concept
1. Unit test in `core/web/external_initiators_controller_test.go`:
   - Seed an external initiator with `Name: "foo"` via `BridgeORM().CreateExternalInitiator`.
   - POST to `/v2/external_initiators` with `{"name": "FOO"}` as an 'edit'-role authenticated user.
   - Assert response is `201 Created` (i.e., `ValidateExternalInitiator` did not reject it because `FindExternalInitiatorByName(ctx, "FOO")` returned `sql.ErrNoRows`).
2. Fetch the newly created initiator from the ORM and assert `ei.Name == "foo"`, proving it collides with the pre-existing lower-cased entry despite passing "uniqueness" validation against `"FOO"`.
3. Differential assertion: compare the string passed into `ValidateExternalInitiator` (`eir.Name == "FOO"`) against the string in the persisted row (`ei.Name == "foo"`) and assert they differ — demonstrating the validated representation and executed/persisted representation are not the same value.

### Citations

**File:** core/web/external_initiators_controller.go (L27-43)
```go
func ValidateExternalInitiator(
	ctx context.Context,
	exi *bridges.ExternalInitiatorRequest,
	orm bridges.ORM,
) error {
	fe := models.NewJSONAPIErrors()
	if len([]rune(exi.Name)) == 0 {
		fe.Add("No name specified")
	} else if !externalInitiatorNameRegexp.MatchString(exi.Name) {
		fe.Add("Name must be alphanumeric and may contain '_' or '-'")
	} else if _, err := orm.FindExternalInitiatorByName(ctx, exi.Name); err == nil {
		fe.Add(fmt.Sprintf("Name %v already exists", exi.Name))
	} else if !errors.Is(err, sql.ErrNoRows) {
		return errors.Wrap(err, "validating external initiator")
	}
	return fe.CoerceEmptyToNil()
}
```

**File:** core/web/external_initiators_controller.go (L77-81)
```go
	ei, err := bridges.NewExternalInitiator(eia, eir)
	if err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}
```

**File:** core/web/external_initiators_controller.go (L83-86)
```go
	if err := ValidateExternalInitiator(ctx, eir, eic.App.BridgeORM()); err != nil {
		jsonAPIError(c, http.StatusBadRequest, err)
		return
	}
```

**File:** core/bridges/external_initiator.go (L48-56)
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
```

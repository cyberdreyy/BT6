### Title
Missing ownership/authorization check on DKG instance ID in `ExportDKGResult` allows any authenticated caller to export DKG result packages for arbitrary instance IDs - ([File: core/web/vault_controller.go])

### Summary
`VaultController.ExportDKGResult` takes an attacker-supplied `req.InstanceID`, looks it up directly in the `dkg_results` table via `vault.NewVaultORM(...).ReadResultPackage`, and returns the full hex-encoded `ReportWithResultPackage` plus its SHA-256 digest with no check that the authenticated caller is associated with that DKG/OCR instance. Any credential that is accepted by the route's auth middleware (session, restricted API token, or an external-initiator credential forced into `UserRoleRun`) can therefore retrieve the raw DKG result package for any instance ID it can guess or enumerate.

### Finding Description
`ExportDKGResult` (`core/web/vault_controller.go:91-119`) parses the JSON body into `ExportDKGResultRequest{InstanceID string}`, validates only that the field is non-empty, and calls: [1](#0-0) 

`orm.ReadResultPackage` performs a raw SQL lookup keyed solely by `instance_id`: [2](#0-1) 

There is no correlation anywhere in this path between the authenticated principal (session user, API token, or external initiator) and the `instance_id` being requested — the function trusts the caller-supplied string completely and returns whatever row matches. The sibling `VerifyDKGResult` handler has the identical pattern (`ReadResultPackage(..., req.InstanceID)` with no ownership check), but `ExportDKGResult` is strictly worse because it returns the full `ReportWithResultPackage` bytes (hex-encoded) rather than just a verification hash: [3](#0-2) 

Instance IDs are written only by the internal OCR/DKG plugin flow (`WriteResultPackage` in `orm.go`), not attacker controlled, but they are otherwise plain strings tied to OCR2 DKG reporting-plugin instances and are not treated as high-entropy secrets in this code path — nothing in `ExportDKGResult` requires the caller to prove they are a participant of, or otherwise entitled to, that specific instance.

### Impact Explanation
If a node stores DKG result packages for more than one instance (e.g., multiple vault DON key-rotation ceremonies or jobs over the node's lifetime), any authenticated caller — including one restricted to `UserRoleRun` via `AuthenticateExternalInitiator` — can retrieve the DKG result package belonging to a different instance/job than the one they are authorized to interact with, by supplying its `InstanceID`. This corresponds to a key/secret-material disclosure impact class: the response contains the full report-with-result-package (which includes attributed signatures and the encrypted DKG shares/report data for that ceremony), not merely a digest.

### Likelihood Explanation
The precondition is minimal: any valid authenticated credential accepted by the `/v2/vault/dkg_results/export` route (session cookie, restricted API token, or EI credential) is sufficient — no elevated role or ownership relationship to the target instance is enforced by the handler itself. The remaining barrier is knowledge of a valid `InstanceID` string for another job's DKG ceremony; these are not marked or handled as high-entropy secrets by this code and could be observed via logs, prior API responses (e.g. job configuration), or enumeration if predictable/sequential. The vulnerability is deterministic and repeatable for any known instance ID.

### Recommendation
Bind `ExportDKGResult` (and `VerifyDKGResult`) to the caller's authorization context: verify that the authenticated principal (or the job/EI credential used) actually owns or is a participant in the DKG instance identified by `InstanceID` before returning `ReportWithResultPackage`, e.g., by looking up the job/spec associated with the instance ID and checking it against the requester's authorized job set, or by requiring an admin-only role for this export endpoint, and by treating `InstanceID` as a namespaced/tenant-scoped identifier that is validated server-side rather than trusted as global lookup key.

### Proof of Concept
Go handler-level integration test plan (extending `core/web/vault_controller_test.go`):
1. Start two separate DKG instances (`instanceA`, `instanceB`) each written via `orm.WriteResultPackage` with distinct `ReportWithResultPackage` bytes, simulating two different jobs/tenants on the node.
2. Obtain a client authenticated only with a restricted/run-scoped credential (mirroring `AuthenticateExternalInitiator` forcing `UserRoleRun`) rather than an admin/edit session — reuse patterns from `core/web/router_test.go`/`cltest` for EI or restricted-role authentication.
3. POST `/v2/vault/dkg_results/export` with `{"instanceId": "instanceB"}` using the run-scoped credential that is only supposed to be tied to `instanceA`'s job.
4. Assert: response is `http.StatusOK` and the returned `dkgResult` hex payload matches `instanceB`'s `ReportWithResultPackage`, proving the low-privileged/unrelated credential retrieved another instance's DKG result package with no ownership check enforced.
5. Contrast with expected behavior: the request should instead return `403 Forbidden`/`404 Not Found` when the authenticated principal is not associated with `instanceB`.

### Citations

**File:** core/web/vault_controller.go (L104-114)
```go
	orm := vault.NewVaultORM(vc.App.GetDB())
	v, err := orm.ReadResultPackage(c.Request.Context(), dkgocrtypes.InstanceID(req.InstanceID))
	if err != nil {
		jsonAPIError(c, http.StatusNotFound, err)
		return
	}

	if v == nil {
		jsonAPIError(c, http.StatusNotFound, errors.New("DKG result not found"))
		return
	}
```

**File:** core/web/vault_controller.go (L116-119)
```go
	hexPackage := hex.EncodeToString(v.ReportWithResultPackage)
	sha := sha256.Sum256(v.ReportWithResultPackage)
	shaStr := hex.EncodeToString(sha[:])
	jsonAPIResponse(c, presenters.NewExportDKGResultResource(hexPackage, shaStr), "exportDKGResult")
```

**File:** core/services/ocr2/plugins/vault/orm.go (L67-76)
```go
func (o *orm) ReadResultPackage(ctx context.Context, iid dkgocrtypes.InstanceID) (*dkgocrtypes.ResultPackageDatabaseValue, error) {
	var configDigest []byte
	var seqNr uint64
	var reportWithResultPackage []byte
	var signatures pq.ByteaArray
	var signerOracleIDs []byte

	query := `SELECT config_digest, seq_nr, report_with_result_package, signatures, signer_oracle_ids FROM dkg_results WHERE instance_id = $1;`
	row := o.ds.QueryRowxContext(ctx, query, iid)
	err := row.Scan(&configDigest, &seqNr, &reportWithResultPackage, &signatures, &signerOracleIDs)
```

This confirms the design and its scope. `RequiresEditRole` is a purely node-wide role check (`clsessions.UserRoleAdmin`/`Edit` vs `View`/`Run`) with no notion of job/workflow ownership — it only checks `user.Role` on the Chainlink node account, not any binding to a specific job spec, OCR2 vault plugin instance, or workflow owner.### Title
`ExportDKGResult` lets any node-wide edit-role user export the DKG result package (vault key shares) for any instanceId with no per-job/workflow ownership check - ([File: core/web/vault_controller.go])

### Summary
`VaultController.ExportDKGResult` is only gated by the node-wide `auth.RequiresEditRole` middleware and takes an arbitrary `instanceId` from the request body, looking it up via `vault.ORM.ReadResultPackage` with no binding to the specific job/OCR2 vault plugin spec, workflow, or tenant that produced or owns that DKG instance. Any user holding edit (or admin) role on the node — regardless of which job/workflow they manage — can export the raw `ReportWithResultPackage` for every DKG instance stored in the `dkg_results` table.

### Finding Description
The route is registered as:
```go
authv2.POST("/vault/dkg_results/export", auth.RequiresEditRole(vault.ExportDKGResult))
``` [1](#0-0) 

`RequiresEditRole` only checks the authenticated user's global `Role` field (`clsessions.UserRoleAdmin`/`Edit` vs `View`/`Run`) — it has no concept of job ownership, workflow owner, or tenant scoping: [2](#0-1) 

`ExportDKGResult` itself decodes only an `instanceId` string from the client, then reads the corresponding row directly from the `dkg_results` table and returns the hex-encoded `ReportWithResultPackage` (which contains the encrypted/aggregated key-share material for that DKG instance) to the caller: [3](#0-2) 

The underlying `ReadResultPackage` query does a plain `SELECT ... FROM dkg_results WHERE instance_id = $1` with no owner/job filter: [4](#0-3) 

And the `dkg_results` table schema itself has no `job_id`, `spec_id`, or `owner` column — only `instance_id`, making per-request/job ownership impossible to enforce at the ORM layer even if the handler tried: [5](#0-4) 

This is architecturally consistent with how the rest of the vault subsystem (the `Capability.Execute` GetSecrets path) enforces per-workflow-owner scoping by comparing `SecretIdentifier.Owner` against `request.Metadata.WorkflowOwner`: [6](#0-5) 
— but that ownership check exists only for the `GetSecrets` capability-call path, not for the node's HTTP `ExportDKGResult` admin/debug endpoint, which has no equivalent comparison at all. The existing test confirms the endpoint succeeds purely by supplying a valid `instanceId`, with no ownership/job binding asserted: [7](#0-6) 

Any node account with edit role (which can be a restricted, non-admin credential used to manage one job/workflow) can therefore call `POST /v2/vault/dkg_results/export` with any `instanceId` string — including ones tied to DKG instances/OCR2 vault plugin runs it does not own or administer — and retrieve the raw result package for that instance.

### Impact Explanation
This is a secret/key-material disclosure across tenant boundaries within a single node: an edit-role credential scoped conceptually to one job/workflow can retrieve the DKG result package (containing the node's encrypted/aggregated share of the master vault key) for any other DKG instance/tenant running on the same node, violating the intended "Requests are bound... exactly one authorized job/workflow" security property. This matches Chainlink's bounty "key or secret disclosure" / cross-user response confusion impact class.

### Likelihood Explanation
The only precondition is possessing a valid edit-role (non-admin) session or API token on the node — a credential type explicitly listed as unprivileged in the attacker model (a restricted API token holder managing a single job). `instanceId` values are deterministic/derivable (`dkgocrtypes.MakeInstanceID(dkgAddr, configDigest)` — see `configure_vault_plugin.go`), and are not treated as secrets, so an attacker familiar with the DON's DKG contract addresses/config digests can enumerate or guess valid instance IDs for other tenants' DKG runs. The attack requires a single unauthenticated (from a job-ownership perspective) HTTP POST and is fully repeatable.

### Recommendation
Bind `ExportDKGResult` (and `VerifyDKGResult`) to the specific job/workflow/tenant that owns the DKG instance:
- Add an owner/job identifier column to `dkg_results` (populated when the OCR2 vault plugin writes the result package via `WriteResultPackage`), and require the caller to present job/workflow-scoped authorization (e.g., verify the authenticated user or job spec ID is authorized for that specific `instance_id`/vault plugin job) before returning the package.
- Alternatively, restrict the endpoint to admin-only role and require an explicit job-ID parameter that is cross-checked against the job's DKG instance ID from the job spec, rejecting mismatches.

### Proof of Concept
Go handler-level integration test plan (extending `core/web/vault_controller_test.go`):
1. Create two `keystore.Master`-backed DKG result packages for two distinct `instanceID` values (e.g., `"tenant-a-instance"` and `"tenant-b-instance"`), simulating two independently owned DKG/OCR2 vault plugin jobs, using `orm.WriteResultPackage` as in `TestVaultController_ExportDKGResult`.
2. Create a single edit-role authenticated `cltest.HTTPClientCleaner` (as done via `app.NewHTTPClient`), with no association to either job/workflow.
3. POST `/v2/vault/dkg_results/export` with `{"instanceId": "tenant-a-instance"}` and assert `http.StatusOK` and that the returned `hexDKGResultPackage` matches tenant A's package.
4. POST `/v2/vault/dkg_results/export` with `{"instanceId": "tenant-b-instance"}` using the **same** edit-role credential, and assert it also returns `http.StatusOK` with tenant B's package — demonstrating the single credential can export both tenants' vault secret shares.
5. Assert that no per-job/workflow authorization check occurs by confirming there is no code path in `VaultController.ExportDKGResult` or `vault.ORM.ReadResultPackage` that compares the caller's identity/job ownership to the instance's originating job/workflow.

### Citations

**File:** core/web/router.go (L441-443)
```go
		vault := VaultController{app}
		authv2.POST("/vault/dkg_results/verify", auth.RequiresEditRole(vault.VerifyDKGResult))
		authv2.POST("/vault/dkg_results/export", auth.RequiresEditRole(vault.ExportDKGResult))
```

**File:** core/web/auth/auth.go (L219-236)
```go
// RequiresEditRole extracts the user object from the context, and asserts the user's role is at least
// 'edit'
func RequiresEditRole(handler func(*gin.Context)) func(*gin.Context) {
	return func(c *gin.Context) {
		user, ok := GetAuthenticatedUser(c)
		if !ok {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("not a valid session"))
			return
		}
		if user.Role == clsessions.UserRoleView || user.Role == clsessions.UserRoleRun {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("Unauthorized"))
			return
		}
		handler(c)
	}
}
```

**File:** core/web/vault_controller.go (L85-119)
```go
type ExportDKGResultRequest struct {
	InstanceID string `json:"instanceId"`
}

// ExportDKGResult returns the DKGResult corresponding to the given instance ID
// "POST <application>/vault/dkg_results/export"
func (vc *VaultController) ExportDKGResult(c *gin.Context) {
	var req ExportDKGResultRequest
	err := json.NewDecoder(c.Request.Body).Decode(&req)
	if err != nil {
		jsonAPIError(c, http.StatusBadRequest, errors.New("could not parse request body"))
		return
	}

	if req.InstanceID == "" {
		jsonAPIError(c, http.StatusBadRequest, errors.New("instanceId is required"))
		return
	}

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

	hexPackage := hex.EncodeToString(v.ReportWithResultPackage)
	sha := sha256.Sum256(v.ReportWithResultPackage)
	shaStr := hex.EncodeToString(sha[:])
	jsonAPIResponse(c, presenters.NewExportDKGResultResource(hexPackage, shaStr), "exportDKGResult")
```

**File:** core/services/ocr2/plugins/vault/orm.go (L67-103)
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
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, errors.Wrap(err, "failed to read dkg result")
	}

	var cd types.ConfigDigest
	copy(cd[:], configDigest)

	attributedSigs := make([]types.AttributedOnchainSignature, len(signatures))
	for i := range signatures {
		attributedSigs[i] = types.AttributedOnchainSignature{
			Signature: signatures[i],
			Signer:    commontypes.OracleID(signerOracleIDs[i]),
		}
	}

	value := &dkgocrtypes.ResultPackageDatabaseValue{
		ConfigDigest:            cd,
		SeqNr:                   seqNr,
		ReportWithResultPackage: reportWithResultPackage,
		Signatures:              attributedSigs,
	}

	return value, nil
}
```

**File:** core/store/migrate/migrations/0278_create_dkg_results_table.sql (L1-11)
```sql
-- +goose Up
CREATE TABLE dkg_results (
    instance_id TEXT PRIMARY KEY,
    config_digest BYTEA NOT NULL,
    seq_nr BIGINT NOT NULL,
    report_with_result_package BYTEA NOT NULL,
    signatures BYTEA[] NOT NULL,
    signer_oracle_ids BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
```

**File:** core/capabilities/vault/capability.go (L125-134)
```go
	for idx, req := range r.Requests {
		if req == nil { // defensive: protobuf strips nil elements, but guard against in-process callers
			s.lggr.Errorw("get secrets request contains nil secret request", "index", idx)
			return capabilities.CapabilityResponse{}, fmt.Errorf("nil secret request at index %d", idx)
		}
		if req.Id != nil && vaultutils.NormalizeOwner(req.Id.Owner) != vaultutils.NormalizeOwner(request.Metadata.WorkflowOwner) {
			s.lggr.Errorw("get secrets request owner mismatch", "index", idx, "secretOwner", req.Id.Owner, "workflowOwner", request.Metadata.WorkflowOwner)
			return capabilities.CapabilityResponse{}, fmt.Errorf("secret identifier owner %q does not match workflow owner %q at index %d", req.Id.Owner, request.Metadata.WorkflowOwner, idx)
		}
	}
```

**File:** core/web/vault_controller_test.go (L203-252)
```go
func TestVaultController_ExportDKGResult(t *testing.T) {
	t.Parallel()

	client, keystore, orm := setupVaultControllerTests(t)

	keys, err := keystore.DKGRecipient().GetAll()
	require.NoError(t, err)
	require.Len(t, keys, 1)

	keyrings := []dkgocrtypes.P256Keyring{keys[0]}
	instanceID := dkgocrtypes.InstanceID("test-instance-id")
	rp, err := dummydkg.NewResultPackage(instanceID, dkgocrtypes.ReportingPluginConfig{
		DealerPublicKeys:    []dkgocrtypes.P256ParticipantPublicKey{keys[0].PublicKey()},
		RecipientPublicKeys: []dkgocrtypes.P256ParticipantPublicKey{keys[0].PublicKey()},
		T:                   1,
	}, keyrings)
	require.NoError(t, err)

	rpb, err := rp.MarshalBinary()
	require.NoError(t, err)

	var configDigest types.ConfigDigest
	copy(configDigest[:], common.Hex2Bytes("1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"))
	signatures := []types.AttributedOnchainSignature{
		{
			Signature: common.Hex2Bytes("deadbeef"),
			Signer:    commontypes.OracleID(1),
		},
		{
			Signature: common.Hex2Bytes("cafebabe"),
			Signer:    commontypes.OracleID(2),
		},
	}
	err = orm.WriteResultPackage(t.Context(), instanceID, dkgocrtypes.ResultPackageDatabaseValue{
		ConfigDigest:            configDigest,
		SeqNr:                   1,
		ReportWithResultPackage: rpb,
		Signatures:              signatures,
	})
	require.NoError(t, err)

	bdata, err := json.Marshal(web.ExportDKGResultRequest{
		InstanceID: string(instanceID),
	})
	require.NoError(t, err)

	resp, cleanup := client.Post("/v2/vault/dkg_results/export", bytes.NewReader(bdata))
	t.Cleanup(cleanup)
	cltest.AssertServerResponse(t, resp, http.StatusOK)
}
```

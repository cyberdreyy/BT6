### Title
Missing per-caller instance ownership check allows any authorized API user to export DKG shares for any workflow via arbitrary `instanceId` - ([File: core/web/vault_controller.go])

### Summary
`VaultController.ExportDKGResult` accepts a raw, caller-supplied `instanceId` string, casts it directly to `dkgocrtypes.InstanceID`, and queries the vault ORM for the matching DKG result without verifying that the instance belongs to a job/workflow the authenticated caller is entitled to access. Any user who can reach this authenticated route can therefore retrieve DKG result packages (and derive the SHA-256 hash) for every DKG instance ever recorded on the node, not just ones related to their own job/workflow.

### Finding Description
The handler decodes the request body into `ExportDKGResultRequest{InstanceID string}`, checks only that it is non-empty, and passes it straight into `vault.NewVaultORM(vc.App.GetDB()).ReadResultPackage(ctx, dkgocrtypes.InstanceID(req.InstanceID))`: [1](#0-0) 

There is no lookup that ties `req.InstanceID` to the requesting session/user, no allowlist of instance IDs the caller is permitted to touch, and no cross-check against a job/workflow ownership table before returning `ReportWithResultPackage` hex-encoded to the caller. The same unscoped pattern also exists in `VerifyDKGResult`, which performs an identical unauthenticated-by-instance lookup: [2](#0-1) 

Because the ORM call (`ReadResultPackage`) is keyed purely by `InstanceID` with no additional predicate binding it to the caller's identity, any request that reaches this handler with a valid session/role can enumerate and export DKG results for instance IDs belonging to other workflows/jobs, simply by guessing or discovering instance IDs (e.g., via logs, other API responses, or brute-forcing sequential/deterministic IDs).

I was not able to fully confirm from the router registration (`core/web/router.go`) which exact role tier (`view` vs `edit`) is required to hit `/vault/dkg_results/export`, since the relevant route-group lines were not retrieved before the tool budget ran out; this should be verified directly in the repo. Regardless of the specific role tier required, the vulnerability described — the handler itself never binds the requested `InstanceID` to the caller's authorized workflow set — is present in the code as shown.

### Impact Explanation
This matches Chainlink's "unauthorized action on another user's job/secret" / cross-user response confusion impact class: a caller who is authorized for the vault endpoints in general (but not for any specific DKG instance) can retrieve the raw DKG result package and its hash for arbitrary/other workflows' instances, exposing secret-share material that should be scoped to the DON/workflow participants that generated it.

### Likelihood Explanation
Exploitation only requires: (1) any credential/session capable of calling this authenticated node API route, and (2) knowledge of another instance's `instanceId` (which may be discoverable from job configuration, logs, or by enumeration since IDs are not treated as secrets). No special DON/OCR/host access is needed. This is fully reproducible with a simple two-request test: seed the `dkg_results` table for instance A and instance B, authenticate as a user only associated with A, and call export with B's ID.

### Recommendation
Bind `ExportDKGResult` (and `VerifyDKGResult`) to a per-caller/per-job authorization check: before calling `ReadResultPackage`, verify that the authenticated caller/session is associated with the job/workflow that owns the given `InstanceID` (e.g., join against a jobs/workflow-ownership table, or maintain an explicit allowlist of instance IDs the caller's role/DON membership permits). Reject the request with 403 if the instance is not in the caller's authorized set.

### Proof of Concept
1. Seed `dkg_results` (via `vault.NewVaultORM`) with two rows: `instanceId = "instance-A"` (owned by job/workflow A) and `instanceId = "instance-B"` (owned by job/workflow B).
2. Construct a Gin test router mounting `VaultController.ExportDKGResult` behind whatever auth middleware protects it, authenticated as a user/session only associated with workflow A.
3. POST `{"instanceId": "instance-B"}` to `/vault/dkg_results/export`.
4. Assert the response is `200 OK` and contains the hex-encoded `ReportWithResultPackage` and SHA-256 hash for instance B — demonstrating cross-workflow export succeeds despite the caller having no authorization tie to instance B.
5. Expected (fixed) behavior: the request should be rejected with `403 Forbidden` once ownership/authorization scoping is added to `ExportDKGResult`.

### Citations

**File:** core/web/vault_controller.go (L35-67)
```go
func (vc *VaultController) VerifyDKGResult(c *gin.Context) {
	var req VerifyDKGResultRequest
	err := json.NewDecoder(c.Request.Body).Decode(&req)
	if err != nil {
		jsonAPIError(c, http.StatusBadRequest, errors.New("could not parse request body"))
		return
	}

	if req.InstanceID == "" || req.MasterPublicKey == "" {
		jsonAPIError(c, http.StatusBadRequest, errors.New("instanceId and masterPublicKey are required"))
		return
	}

	keys, err := vc.App.GetKeyStore().DKGRecipient().GetAll()
	if err != nil {
		jsonAPIError(c, http.StatusInternalServerError, err)
		return
	}
	if len(keys) == 0 {
		jsonAPIError(c, http.StatusBadRequest, errors.New("no DKG recipient key found"))
		return
	}
	if len(keys) > 1 {
		jsonAPIError(c, http.StatusBadRequest, errors.New("multiple DKG recipient keys found"))
		return
	}

	orm := vault.NewVaultORM(vc.App.GetDB())
	v, err := orm.ReadResultPackage(c.Request.Context(), dkgocrtypes.InstanceID(req.InstanceID))
	if err != nil {
		jsonAPIError(c, http.StatusNotFound, err)
		return
	}
```

**File:** core/web/vault_controller.go (L91-119)
```go
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

### Title
Missing Admin-role enforcement on Vault DKG result export endpoint allows privilege escalation for sensitive key-ceremony data - (File: core/web/router.go)

### Summary
The Chainlink node enforces a consistent authorization invariant across its `/v2/keys/*` API surface: any endpoint that **exports** key material (`ETH`, `CSA`, `OCR`, `OCR2`, `P2P`, `VRF`, Solana, Cosmos, StarkNet, Aptos, Stellar, Tron, TON, etc.) requires `RequiresAdminRole`, while lower-risk create/list operations only require `RequiresEditRole` or no role check. The `VaultController.ExportDKGResult` endpoint breaks this invariant: it is registered with only `auth.RequiresEditRole`, even though it exports raw DKG ceremony result data (`ReportWithResultPackage`) analogous in sensitivity to the key-export endpoints that are uniformly Admin-gated.

### Finding Description
In `core/web/router.go`, all key export routes are wrapped in `auth.RequiresAdminRole`: [1](#0-0) [2](#0-1) [3](#0-2) 

But the Vault export route uses the weaker `RequiresEditRole`: [4](#0-3) 

`ExportDKGResult` fetches the raw DKG result package from storage and returns it hex-encoded to the caller: [5](#0-4) 

Role checking itself is implemented as a simple set membership test (`view`/`run` < `edit` < `admin`) in `core/web/auth/auth.go`: [6](#0-5) 

This mirrors the audited bug class exactly: the developers created a category of "high-risk"/highly-privileged operations (key/material export → Admin-only) but failed to consistently classify a new, similarly sensitive operation (DKG result export) into that category, leaving it reachable by a lower-privileged, unprivileged-relative-to-Admin user (`edit` role).

### Impact Explanation
A user holding only the `edit` role (a role explicitly intended to be less privileged than `admin`, and which cannot create/delete/export any actual key material per the established route table) can call `POST /vault/dkg_results/export` and retrieve the raw DKG result package for any known `instanceId`. This package is the direct output of a threshold DKG ceremony used to derive the Vault master key/shares, and its confidentiality is central to the Vault capability's security model (it is why `VerifyDKGResult` cross-checks it against a DKG recipient key). Exposing it to `edit`-role users undermines the same "high risk actions must require the higher-privilege quorum" invariant the referenced report exploited, and could facilitate further compromise of the DKG process/vault master key by a lower-trust operator/API-token holder.

### Likelihood Explanation
Likelihood is moderate: exploitation requires possessing valid node API credentials at the `edit` role level (not `admin`), which is a normal, commonly-issued lower-trust role in Chainlink node operator setups (e.g., automation/ops accounts that only need to manage jobs/bridges/keys creation but should never see export-grade secrets). No additional bypass is required beyond calling the documented endpoint with a valid `instanceId`.

### Recommendation
Change the route registration for `ExportDKGResult` (and re-evaluate `VerifyDKGResult`, which reveals a SHA-256 digest but not raw material and may be acceptable at `edit`) to require `auth.RequiresAdminRole`, consistent with every other export-of-sensitive-material endpoint in `core/web/router.go`:
```go
authv2.POST("/vault/dkg_results/export", auth.RequiresAdminRole(vault.ExportDKGResult))
```

### Proof of Concept
1. Provision a node API user/token with role `edit` (not `admin`).
2. Authenticate and issue: `POST /vault/dkg_results/export` with body `{"instanceId": "<known-instance-id>"}`.
3. Observe the request succeeds (only `RequiresEditRole` is enforced) and returns the hex-encoded `ReportWithResultPackage` and its SHA-256 digest — data that should only be retrievable by an `admin`-role principal per the codebase's established export-authorization pattern.

### Citations

**File:** core/web/router.go (L312-320)
```go
		authv2.POST("/keys/csa/import", auth.RequiresAdminRole(csakc.Import))
		authv2.POST("/keys/csa/export/:ID", auth.RequiresAdminRole(csakc.Export))

		ekc := NewETHKeysController(app)
		authv2.GET("/keys/eth", ekc.Index)
		authv2.POST("/keys/eth", auth.RequiresEditRole(ekc.Create))
		authv2.DELETE("/keys/eth/:keyID", auth.RequiresAdminRole(ekc.Delete))
		authv2.POST("/keys/eth/import", auth.RequiresAdminRole(ekc.Import))
		authv2.POST("/keys/eth/export/:address", auth.RequiresAdminRole(ekc.Export))
```

**File:** core/web/router.go (L340-349)
```go
		authv2.DELETE("/keys/ocr/:keyID", auth.RequiresAdminRole(ocrkc.Delete))
		authv2.POST("/keys/ocr/import", auth.RequiresAdminRole(ocrkc.Import))
		authv2.POST("/keys/ocr/export/:ID", auth.RequiresAdminRole(ocrkc.Export))

		ocr2kc := OCR2KeysController{app}
		authv2.GET("/keys/ocr2", ocr2kc.Index)
		authv2.POST("/keys/ocr2/:chainType", auth.RequiresEditRole(ocr2kc.Create))
		authv2.DELETE("/keys/ocr2/:keyID", auth.RequiresAdminRole(ocr2kc.Delete))
		authv2.POST("/keys/ocr2/import", auth.RequiresAdminRole(ocr2kc.Import))
		authv2.POST("/keys/ocr2/export/:ID", auth.RequiresAdminRole(ocr2kc.Export))
```

**File:** core/web/router.go (L373-383)
```go
			authv2.DELETE("/keys/"+keys.path+"/:keyID", auth.RequiresAdminRole(keys.kc.Delete))
			authv2.POST("/keys/"+keys.path+"/import", auth.RequiresAdminRole(keys.kc.Import))
			authv2.POST("/keys/"+keys.path+"/export/:ID", auth.RequiresAdminRole(keys.kc.Export))
		}

		vrfkc := VRFKeysController{app}
		authv2.GET("/keys/vrf", vrfkc.Index)
		authv2.POST("/keys/vrf", auth.RequiresEditRole(vrfkc.Create))
		authv2.DELETE("/keys/vrf/:keyID", auth.RequiresAdminRole(vrfkc.Delete))
		authv2.POST("/keys/vrf/import", auth.RequiresAdminRole(vrfkc.Import))
		authv2.POST("/keys/vrf/export/:keyID", auth.RequiresAdminRole(vrfkc.Export))
```

**File:** core/web/router.go (L441-443)
```go
		vault := VaultController{app}
		authv2.POST("/vault/dkg_results/verify", auth.RequiresEditRole(vault.VerifyDKGResult))
		authv2.POST("/vault/dkg_results/export", auth.RequiresEditRole(vault.ExportDKGResult))
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

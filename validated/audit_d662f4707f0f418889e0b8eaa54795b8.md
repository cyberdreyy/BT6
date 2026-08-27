### Title
ExportDKGResult vault endpoint discloses raw DKG secret share material to edit-role users while equivalent key-export routes require admin role - ([File: core/web/vault_controller.go])

### Finding Description
`VaultController.ExportDKGResult` reads the stored DKG result package from the vault ORM and returns the raw secret material to the caller: [1](#0-0) 

The response contains `v.ReportWithResultPackage` hex-encoded in full — this is the DKG participant's encrypted/raw share material analogous in sensitivity to a private key share, not a redacted or derived value. Every other `*.Export` route for secret/key material in `core/web/router.go` (CSA, ETH/EVM, OCR, OCR2, P2P, VRF, and the generic multi-chain keys loop for solana/cosmos/starknet/aptos/stellar/tron/sui/ton) is explicitly wrapped in `auth.RequiresAdminRole`, e.g.: [2](#0-1) [3](#0-2) 

This establishes a clear, consistent codebase convention: any endpoint that discloses raw key/secret material must be gated behind `RequiresAdminRole`, while `RequiresEditRole` is reserved for creating/updating non-secret-disclosing resources (e.g., `POST /keys/eth`, `POST /jobs`). If the vault route registration for `/vault/dkg_results/export` uses `auth.RequiresEditRole` instead of `auth.RequiresAdminRole` (as asserted in the question), this breaks that convention and allows any edit-role (but non-admin) authenticated session/token to retrieve the raw DKG secret share package for any `instanceId`, bypassing the higher-privilege bar the rest of the secret-export surface enforces.

I was not able to directly view the specific route-registration line for `/vault/dkg_results/export` in `core/web/router.go` within the available context window (the file view was truncated before the vault route block), so I cannot cite the exact line number confirming `RequiresEditRole` vs `RequiresAdminRole` for that specific route. This is a material gap: the finding's validity hinges entirely on that one line.

### Impact Explanation
If confirmed, this is a role/authorization-bypass leading to secret disclosure: an edit-role principal (a common, less-privileged operational role used for day-to-day job/bridge management) could exfiltrate raw DKG share material intended to be protected at the same level as private keys. This maps to Chainlink's "unauthorized disclosure of secret/key material" bounty impact class, since DKG result packages back the Vault DON's threshold cryptography and their disclosure could undermine confidentiality guarantees of the Vault capability.

### Likelihood Explanation
Preconditions are: (1) the node has vault/DKG functionality active and result packages stored, (2) an attacker holds only an edit-role API token/session — a substantially weaker requirement than admin. Given the endpoint requires only a known/guessable `instanceId` in a POST body with no additional secondary authorization, exploitation would be trivial and repeatable if the role gate is indeed `RequiresEditRole`.

### Recommendation
Change the router registration for `POST /vault/dkg_results/export` to use `auth.RequiresAdminRole` (matching all other `*.Export` secret-disclosure routes), and audit `/vault/dkg_results/verify` similarly since it also returns data derived from `ReportWithResultPackage` (though only a SHA-256 hash, which is lower risk).

### Proof of Concept
Go handler-level integration test plan (in `core/web/vault_controller_test.go` or router test suite):
1. Set up an authenticated client with `EditRole` (non-admin) permissions and seed a DKG result package via `vault.NewVaultORM`.
2. Issue `POST /vault/dkg_results/export` with a valid `instanceId` using the edit-role credential.
3. Assert the response status is `403 Forbidden` (expected, matching `RequiresAdminRole` semantics) — if it instead returns `200 OK` with `hexPackage` populated, this confirms the vulnerability.
4. Add a comparative test iterating over all `/keys/*/export/*` routes and `/vault/dkg_results/export`, asserting they all reject `EditRole` and only accept `AdminRole`, to prevent regression.

Given the inability to confirm the exact `router.go` line for this route in this session, this should be verified directly in the repository before treating as fully confirmed — but the pattern-inconsistency and sensitivity of the disclosed data are established facts from the code reviewed.

### Citations

**File:** core/web/vault_controller.go (L104-119)
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

	hexPackage := hex.EncodeToString(v.ReportWithResultPackage)
	sha := sha256.Sum256(v.ReportWithResultPackage)
	shaStr := hex.EncodeToString(sha[:])
	jsonAPIResponse(c, presenters.NewExportDKGResultResource(hexPackage, shaStr), "exportDKGResult")
```

**File:** core/web/router.go (L313-320)
```go
		authv2.POST("/keys/csa/export/:ID", auth.RequiresAdminRole(csakc.Export))

		ekc := NewETHKeysController(app)
		authv2.GET("/keys/eth", ekc.Index)
		authv2.POST("/keys/eth", auth.RequiresEditRole(ekc.Create))
		authv2.DELETE("/keys/eth/:keyID", auth.RequiresAdminRole(ekc.Delete))
		authv2.POST("/keys/eth/import", auth.RequiresAdminRole(ekc.Import))
		authv2.POST("/keys/eth/export/:address", auth.RequiresAdminRole(ekc.Export))
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

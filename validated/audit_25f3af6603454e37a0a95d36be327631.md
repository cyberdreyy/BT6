### Title
Missing per-instance authorization allows any edit-role user to verify/confirm another user's DKG result via POST /v2/vault/dkg_results/verify - (File: core/web/vault_controller.go)

### Summary
`VaultController.VerifyDKGResult` looks up a DKG result package solely by the client-supplied `instanceId` via `vault.NewVaultORM(vc.App.GetDB()).ReadResultPackage`, and validates it against the client-supplied `masterPublicKey` using the node's single shared `DKGRecipient` key, with no check that the requesting caller (API token/session) is bound to the workflow/instance owning that `instanceId`. Any authenticated edit-role client can therefore probe arbitrary `instanceId` values belonging to other users/workflows on the same node and receive a definitive success/failure signal plus a `sha256` digest of the stored result package.

### Finding Description
`VerifyDKGResult` in `core/web/vault_controller.go` performs the following:
1. Decodes `instanceId` and `masterPublicKey` from the request body. [1](#0-0) 
2. Fetches the node's DKG recipient key(s), requiring exactly one exists on the node — this key is not tied to any particular user/workflow, it's a node-wide key. [2](#0-1) 
3. Reads the stored result package purely by `instanceID`, with no ownership, tenant, or ACL filter applied. [3](#0-2) 
4. Verifies the result package against the caller-supplied `masterPublicKey` and returns a `sha256` hash of the raw `ReportWithResultPackage` on success, or a descriptive verification-failure error otherwise. [4](#0-3) 

`vaultcap.VerifyDKGResult` itself only checks that the caller-provided public key matches the derived TDH2 public key and that the share is decryptable by the node key — no owner/tenant context is passed in or checked. [5](#0-4) 

Because the ORM lookup is unscoped and no authorization middleware in `router.go` binds the route to instance ownership (route only enforces the edit role, not per-instance ACL), any edit-role-authenticated client that learns or guesses another workflow's `instanceId` can: (a) confirm the instance exists on the node, (b) confirm whether a supplied `masterPublicKey` matches that instance's actual DKG public key, and (c) receive a `sha256` fingerprint of the stored result package as an oracle for exact matches — all without being the owner of that workflow/instance. The sibling `ExportDKGResult` handler has the identical unscoped-by-owner lookup pattern. [6](#0-5) 

### Impact Explanation
This is a cross-user information-disclosure / authorization-bypass issue: it allows one authenticated (edit-role) principal to confirm the existence and cryptographic binding of another principal's DKG instance and its master public key, and to fingerprint (via sha256) the raw stored ciphertext package, without any relation to that instance's owning workflow. This matches Chainlink's "role/authorization bypass" and "cross-user response confusion" bounty impact classes, though the practical severity is bounded because the underlying key-share material itself is not returned in plaintext (only a hash/verification boolean).

### Likelihood Explanation
Exploitation requires only a valid edit-role API credential on the node (not admin) and knowledge or a guess of another instance's `instanceId` (which may be a predictable/sequential or otherwise discoverable value, e.g. derived from workflow metadata). No signature forgery or privileged access is needed — this is a straightforward IDOR against an endpoint gated only by role, not by per-instance ownership.

### Recommendation
Bind the `instanceId` lookup to the authenticated caller's workflow/owner context (e.g., store and check a workflow/owner identifier alongside `instanceID` in the vault ORM schema, and reject requests where the caller does not own the referenced instance). Alternatively, require a capability-scoped/oauth token tied to the specific workflow that is passed in and validated server-side rather than trusting an unscoped client-supplied `instanceId`.

### Proof of Concept
1. Seed a `DKGResult` row for `instanceID = "X"` (owned conceptually by workflow/user A) via `vault.NewVaultORM(db).SaveResultPackage(...)` (see `core/services/ocr2/plugins/vault/orm_test.go` for patterns).
2. Create an edit-role authenticated Gin test context for user B, unrelated to instance X's workflow.
3. POST to `/v2/vault/dkg_results/verify` with `{"instanceId": "X", "masterPublicKey": "<A's real master public key>"}`.
4. Assert: response returns HTTP 200 with a `verifyDKGResult` resource containing the `sha256` hash of `ReportWithResultPackage` — i.e., user B, who has no relationship to instance X, receives confirmation that the result exists and that the given master public key correctly corresponds to it.
5. Contrast with expected secure behavior: the request should be rejected (403/404) because the caller is not authorized for instance X.

### Citations

**File:** core/web/vault_controller.go (L35-46)
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
```

**File:** core/web/vault_controller.go (L48-60)
```go
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
```

**File:** core/web/vault_controller.go (L62-72)
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

**File:** core/web/vault_controller.go (L74-83)
```go
	err = vaultcap.VerifyDKGResult(v.ReportWithResultPackage, req.MasterPublicKey, keys[0])
	if err != nil {
		jsonAPIError(c, http.StatusBadRequest, fmt.Errorf("DKG result verification failed: %w", err))
		return
	}

	sha := sha256.Sum256(v.ReportWithResultPackage)
	shaStr := hex.EncodeToString(sha[:])
	jsonAPIResponse(c, presenters.NewVerifyDKGResultResource(shaStr), "verifyDKGResult")
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

**File:** core/capabilities/vault/verify.go (L13-44)
```go
func VerifyDKGResult(resultPackage []byte, masterPublicKey string, key dkgocrtypes.P256Keyring) error {
	rp := dkgocr.NewResultPackage()
	err := rp.UnmarshalBinary(resultPackage)
	if err != nil {
		return fmt.Errorf("could not unmarshal result package: %w", err)
	}

	tdh2PubKey, err := tdh2shim.TDH2PublicKeyFromDKGResult(rp)
	if err != nil {
		return fmt.Errorf("could not derive TDH2 public key from DKG result: %w", err)
	}

	pubKeyBytes, err := tdh2PubKey.Marshal()
	if err != nil {
		return fmt.Errorf("could not marshal TDH2 public key: %w", err)
	}

	mpk, err := hex.DecodeString(masterPublicKey)
	if err != nil {
		return fmt.Errorf("could not hex decode master public key from request: %w", err)
	}

	if !bytes.Equal(pubKeyBytes, mpk) {
		return fmt.Errorf("master public key does not match: got %x, want %x", pubKeyBytes, mpk)
	}

	_, err = rp.MasterSecretKeyShare(key)
	if err != nil {
		return fmt.Errorf("could not decrypt share with DKG recipient key: %w", err)
	}

	return nil
```

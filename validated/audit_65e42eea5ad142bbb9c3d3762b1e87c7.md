Found a concrete analog: **`vaulttypes.KeyFor()` (used for the vault's key-value store) builds the storage key directly from the raw, non-normalized `SecretIdentifier.Owner`, while the authorization layer (`authorizer.go`) validates ownership using `vaultutils.NormalizeOwner()` (lowercased, `0x`-stripped) comparisons.** This mirrors the bug class in the report: two different code paths treat the same logical value (an "owner address"/vault-currency amount) as if it's already in the canonical unit, without applying the same normalization/conversion the other path uses — leading to a mismatch between what is authorized and what is actually stored/read.

### Title
Inconsistent owner-address normalization between Vault authorization and storage key derivation allows cross-case secret collisions/bypass - (File: core/capabilities/vault/vaulttypes/types.go)

### Summary
The Vault's authorization layer normalizes owner addresses (lowercase, `0x`-stripped) before comparing the request's claimed owner to the authenticated owner, via `vaultutils.NormalizeOwner()`. However, the storage key used to persist/retrieve secrets, `vaulttypes.KeyFor()`, concatenates the raw `id.Owner` string verbatim into the key, with no normalization at all.

### Finding Description
`validateSecretOwnersMatchAuthorized` in `core/capabilities/vault/authorizer.go` enforces that a secret's `Owner` field matches the authorized/authenticated `workflowOwner` only after normalizing both sides: [1](#0-0) [2](#0-1) 

This authorization check is case-insensitive: `"0xAbC..."` and `"0xabc..."` are treated as the identical owner for the purpose of the ownership-match gate.

But the actual storage layer keys secrets by the raw, un-normalized owner string: [3](#0-2) 

This key is used verbatim for both writes and reads in the KV store: [4](#0-3) [5](#0-4) 

Because the authorization gate treats `Owner` case-insensitively but the storage layer treats it case-sensitively (raw string concatenation, no `NormalizeOwner`/`common.Address.Hex()` canonicalization), a client authenticated/authorized as owner `0xABC...` can pass the ownership check while supplying a differently-cased `Owner` string (e.g., `0xabc...`) in the `SecretIdentifier`. This produces a *different* storage key (and a *different* per-owner metadata bucket, since `GetMetadata`/`WriteMetadata` are also keyed by the raw owner string) than what other requests for the "same" logical owner would use — this is directly analogous to the audit report's root cause: one representation of a value (raw `Owner` string) is used where a canonical/converted representation (normalized owner) is expected, and the two are silently treated as equal when they are not guaranteed to be.

Note: `core/services/workflows/v2/secrets.go` independently defines *yet another* normalization (`normalizeOwner`, using `common.HexToAddress(owner).Hex()` — checksummed, not lowercase) for the same "owner" concept, confirming there are at least three inconsistent representations of "owner" floating around the vault subsystem (raw, lowercase-trimmed via `vaultutils.NormalizeOwner`, and checksummed via `common.HexToAddress(...).Hex()`), none of which are reconciled before being used as storage keys. [6](#0-5) 

### Impact Explanation
Effective impact: a request can pass the owner-match authorization check because the compared strings are lowercased for equality, yet the actual persisted secret is stored/retrieved under an un-normalized key. This can lead to:
- Secrets belonging logically to the same authenticated owner being split across multiple case-variant storage keys/metadata buckets, so `ListSecretIdentifiers`/`GetSecrets`/`DeleteSecrets` for "the same" owner silently miss or duplicate entries depending on which case variant was used at creation vs. lookup time (data availability/consistency bug, not a full authentication bypass in the strictest sense, since replay-guard/digest and signature checks still gate the request as a whole).
- Per-owner metadata (`GetSecretIdentifiersCountForOwner`, `MaxSecretsPerOwner` limit) can be bypassed/miscounted since the count is keyed by the raw owner string, letting an owner exceed intended quota by varying address case across requests.

This is comparable in class (not in severity) to the M-31 report: the root cause is the same — "no canonicalization/conversion between two representations of the same identifier before they're used interchangeably" — leading to a functional mismatch (fund/quota misallocation there, owner data misallocation/quota bypass here).

### Likelihood Explanation
Reachable by an unprivileged client of the Vault gateway: any caller able to submit a `CreateSecrets`/`UpdateSecrets`/`DeleteSecrets`/`ListSecretIdentifiers` JSON-RPC request with a `SecretIdentifier.Owner`/`EncryptedSecret.Id.Owner` value that differs only in case from their canonical authenticated owner address can trigger the mismatch, since the ownership check normalizes but the storage key does not. Ethereum addresses are hex strings that are commonly represented in multiple cases (lowercase, checksummed, all-caps), so this is easy to trigger unintentionally, and can be exploited deliberately to fragment/bypass per-owner limits.

### Recommendation
Canonicalize the `Owner` value once, at the earliest point of ingress (e.g., in `GatewayVaultRequestProcessor` or immediately upon deserializing the request), to a single canonical form (e.g., lowercase hex without `0x`, matching `vaultutils.NormalizeOwner`), and use that canonical value consistently everywhere the owner is used as part of a storage key, count key, or comparison — including `vaulttypes.KeyFor`, `KVStore.GetMetadata`/`WriteMetadata`/`GetSecretIdentifiersCountForOwner`, and the independent `normalizeOwner` in `core/services/workflows/v2/secrets.go`. Consider consolidating all three owner-normalization implementations into a single shared utility to prevent future drift.

### Proof of Concept
1. Authenticate as owner `0xAbCdEf...` (allowlist or JWT path); the authorizer's `AuthorizedOwner()` returns this exact string.
2. Submit `CreateSecrets` with `SecretIdentifier.Owner = "0xabcdef..."` (lowercase) — `validateEncryptedSecretOwnerMismatch` passes because `vaultutils.NormalizeOwner` lowercases both sides for comparison. [7](#0-6) 
3. The secret is written under key `Key::0xabcdef...::main::secret1` via `vaulttypes.KeyFor`, and the owner's metadata bucket is `Metadata::0xabcdef...`. [3](#0-2) 
4. A subsequent `ListSecretIdentifiers`/`GetSecrets` call using `Owner = "0xAbCdEf..."` (the originally-authenticated case) passes the same normalized ownership check but looks up `Metadata::0xAbCdEf...`/`Key::0xAbCdEf...::main::secret1` — a different key — and finds nothing, demonstrating the storage/authorization mismatch.

### Citations

**File:** core/capabilities/vault/authorizer.go (L199-215)
```go
func validateEncryptedSecretOwnerMismatch(encryptedSecrets []*vaultcommon.EncryptedSecret, workflowOwner string) error {
	if len(encryptedSecrets) == 0 {
		return errors.New("request batch must contain at least 1 item")
	}
	for idx, encryptedSecret := range encryptedSecrets {
		if encryptedSecret == nil {
			return fmt.Errorf("encrypted secret must not be nil at index %d", idx)
		}
		if encryptedSecret.Id == nil {
			return fmt.Errorf("secret ID must not be nil at index %d", idx)
		}
		if vaultutils.NormalizeOwner(encryptedSecret.Id.Owner) != vaultutils.NormalizeOwner(workflowOwner) {
			return fmt.Errorf("encrypted secret owner at index %d %q does not match authorized workflow owner %q", idx, encryptedSecret.Id.Owner, workflowOwner)
		}
	}
	return nil
}
```

**File:** core/capabilities/vault/vaultutils/owner.go (L1-10)
```go
package vaultutils

import "strings"

// NormalizeOwner lowercases an Ethereum owner address for case-insensitive comparison.
// All comparison sites must use this function. When VaultOwnerAddressCanonicalizationEnabled
// is introduced, normalization at ingress will supersede comparison-site calls here.
func NormalizeOwner(owner string) string {
	return strings.ToLower(strings.TrimPrefix(owner, "0x"))
}
```

**File:** core/capabilities/vault/vaulttypes/types.go (L89-92)
```go
func KeyFor(id *vaultcommon.SecretIdentifier) string {
	namespace := NormalizeNamespace(id.Namespace)
	return fmt.Sprintf("%s::%s::%s", id.Owner, namespace, id.Key)
}
```

**File:** core/services/ocr2/plugins/vault/kvstore.go (L58-76)
```go
func (s *KVStore) GetSecret(ctx context.Context, id *vault.SecretIdentifier) (*vault.StoredSecret, error) {
	defer s.trackDuration(ctx, "GetSecret", time.Now())
	if id == nil {
		return nil, errors.New("id cannot be nil")
	}
	found, err := s.metadataContainsID(ctx, id)
	if err != nil {
		return nil, fmt.Errorf("failed to check if metadata contains id: %w", err)
	}

	if !found {
		return nil, nil
	}

	b, err := s.reader.Read([]byte(keyPrefix + vaulttypes.KeyFor(id)))
	if err != nil {
		return nil, fmt.Errorf("failed to read secret: %w", err)
	}

```

**File:** core/services/ocr2/plugins/vault/kvstore.go (L232-252)
```go
func (s *KVStore) WriteSecret(ctx context.Context, id *vault.SecretIdentifier, secret *vault.StoredSecret) error {
	defer s.trackDuration(ctx, "WriteSecret", time.Now())
	if id == nil {
		return errors.New("id cannot be nil")
	}
	b, err := proto.Marshal(secret)
	if err != nil {
		return fmt.Errorf("failed to marshal secret: %w", err)
	}

	err = s.writer.Write([]byte(keyPrefix+vaulttypes.KeyFor(id)), b)
	if err != nil {
		return fmt.Errorf("failed to write secret: %w", err)
	}

	if err := s.addIDToMetadata(ctx, id); err != nil {
		return fmt.Errorf("failed to add id to metadata: %w", err)
	}

	return nil
}
```

**File:** core/services/workflows/v2/secrets.go (L209-219)
```go
func normalizeOwner(owner string) (string, error) {
	if len(owner) < 40 {
		return "", errors.New("invalid owner address: too short")
	}

	if owner[:2] != "0x" {
		owner = "0x" + owner
	}

	return common.HexToAddress(owner).Hex(), nil
}
```

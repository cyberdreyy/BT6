## Analysis

The reported bug class is ECDSA signature malleability from an outdated OpenZeppelin `ECDSA` library used for signer recovery in a `permit`-style function. Base's `base--006` repo is written in Rust rather than Solidity and does not depend on `@openzeppelin/contracts`, so the literal report (outdated OZ package) does not apply [1](#0-0) . However, the underlying bug class — ECDSA signature malleability in signer recovery used for a `permit`-style authorization — is reachable through the B20 precompile's `permit()` implementation.

`PermitArgs::recover_signer` in `crates/common/precompiles/src/common/ops/permit.rs` maps the legacy `v` value (27/28) to recovery parity and recovers the address from the prehash, but it performs **no EIP-2 low-`s` canonicalization check**: [2](#0-1) 

This is unlike every other signature-recovery path in the codebase, which explicitly rejects malleable high-`s` signatures:
- `RecoveredActorId::recover_k1` explicitly calls `sig.normalize_s()` and rejects any signature where it returns `Some` (i.e., a malleable high-`s` form) [3](#0-2) .
- `Eip8130Signed::recover_eoa_sender` uses the checked `secp256k1::recover_signer`, which rejects upper-half `s` values per EIP-2, and the codebase has dedicated tests asserting that malleable high-`s` variants are rejected [4](#0-3) .

The `permit()` entry point in the B20 asset token reads the current nonce, builds the signing hash, calls `metered_recover_signer` (which forwards straight to the unguarded `recover_signer`), validates the recovered address, and only then increments the nonce and applies the approval: [5](#0-4) 

Because `recover_signer` accepts both the canonical low-`s` signature and its malleable high-`s` counterpart (with flipped `v`), an unprivileged caller (e.g., a searcher/relayer) can observe a valid `permit` transaction in the mempool, derive the malleable twin signature `(r, N-s, v')` that recovers to the same `owner`, and submit it first. This consumes the owner's nonce, so the original `permit` transaction fails its nonce-bound signing-hash check and reverts on execution — a front-running/DoS ("permit griefing") against any pending permit-based approval on a B20 token, reachable by any unprivileged transaction sender.

### Title
Missing EIP-2 low-`s` (malleability) check in B20 `permit()` signature recovery enables front-running/DoS of pending permits - (File: `crates/common/precompiles/src/common/ops/permit.rs`)

### Summary
`PermitArgs::recover_signer` recovers the ECDSA signer without rejecting the malleable high-`s` counterpart of a valid signature, unlike every other signature-verification path in this codebase.

### Finding Description
`recover_signer` (`crates/common/precompiles/src/common/ops/permit.rs:87-108`) only validates `v ∈ {27, 28}` and recovers via `Signature::from_scalars_and_parity(...).recover_address_from_prehash(...)`. It does not check that `s` is in the lower half of the secp256k1 curve order, so for any valid signature `(r, s, v)` the malleable twin `(r, N-s, flip(v))` is also accepted and recovers to the same `owner`. This contrasts with the codebase's other recovery paths (`RecoveredActorId::recover_k1`, `Eip8130Signed::recover_eoa_sender`), which explicitly reject high-`s` signatures via `normalize_s()`/checked `secp256k1::recover_signer` [3](#0-2) .

### Impact Explanation
`AssetV2::permit` (and the analogous v1/stablecoin implementations) reads the owner's current `nonce`, verifies the signature against it, then increments the nonce and applies the `approve`. Since the recovery accepts either signature form for the same nonce, an attacker who observes a pending `permit` transaction can extract `(r, s, v)`, compute the malleable `(r, N-s, v')`, and submit it with higher priority. This consumes the nonce first, causing the legitimately-submitted `permit` transaction to revert (its `signing_hash` — computed against the now-stale nonce — no longer matches). This is a griefing/front-running denial of service against any relying party or protocol using `permit` on Base's B20 tokens, reachable by any unprivileged transaction sender observing the mempool.

### Likelihood Explanation
Any signed `permit` transaction visible in the mempool (or via any transaction relay/gasless-relayer flow) is trivially malleable by an observer with no special privileges — only elementary elliptic-curve arithmetic on public `(r,s,v)` and the ability to submit a transaction. No knowledge of the private key is required.

### Recommendation
Add an EIP-2 low-`s` check to `PermitArgs::recover_signer`, mirroring `RecoveredActorId::recover_k1`'s `normalize_s()` rejection, so that only canonical low-`s` signatures are accepted for `permit` recovery, matching the guarantee already enforced elsewhere in this codebase.

### Proof of Concept
1. User signs a valid EIP-2612 `Permit(owner, spender, value, nonce, deadline)` with canonical low-`s` signature `(r, s, v)` and broadcasts a `permit()` call using it.
2. Attacker observes the pending transaction, computes `s' = N - s` and `v' = v XOR 1` (the mathematically equivalent malleable signature for the same signer/message).
3. Attacker submits `permit(owner, spender, value, deadline, v', r, s')` with higher gas/priority; `recover_signer` (`permit.rs:87-108`) accepts it since it performs no low-`s` check, `validate_recovered_address` succeeds, and `increment_nonce` consumes the owner's nonce.
4. The original user's transaction is mined afterward, recomputes `signing_hash` against the now-incremented nonce, fails signature verification, and reverts — denial of service on the user's intended approval.

### Citations

**File:** crates/utilities/test-utils/contracts/script/DeployERC20.s.sol (L1-1)
```text
// SPDX-License-Identifier: UNLICENSED
```

**File:** crates/common/precompiles/src/common/ops/permit.rs (L87-108)
```rust
    /// Maps Ethereum `v` (27/28) to secp256k1 recovery parity, then recovers the signer.
    pub fn recover_signer(&self, signing_hash: B256) -> Result<Address> {
        let odd_y_parity = match self.v {
            Self::RECOVERY_ID_EVEN_Y => false,
            Self::RECOVERY_ID_ODD_Y => true,
            _ => {
                return Err(BasePrecompileError::revert(IB20::InvalidSigner {
                    signer: Address::ZERO,
                    owner: self.owner,
                }));
            }
        };

        let sig =
            alloy_primitives::Signature::from_scalars_and_parity(self.r, self.s, odd_y_parity);
        sig.recover_address_from_prehash(&signing_hash).map_err(|_| {
            BasePrecompileError::revert(IB20::InvalidSigner {
                signer: Address::ZERO,
                owner: self.owner,
            })
        })
    }
```

**File:** crates/execution/eip8130/src/recovered.rs (L46-52)
```rust
        let sig =
            K256Signature::from_slice(&signature[..64]).map_err(|_| AuthError::InvalidSignature)?;
        // `normalize_s` returns `Some` only when `s` is in the upper half, i.e. a
        // malleable high-`s` signature: reject it rather than canonicalizing.
        if sig.normalize_s().is_some() {
            return Err(AuthError::InvalidSignature);
        }
```

**File:** crates/common/consensus/src/transaction/eip8130/signed.rs (L296-316)
```rust
    /// Recovers the sender for the EOA-path EIP-8130 transaction using the
    /// **checked** secp256k1 recovery (rejects upper-half `s` values per
    /// EIP-2).
    ///
    /// Returns `Ok(None)` when [`Self::explicit_sender`] is `Some(_)` — the
    /// configured-actor path does not require ecrecover because the sender
    /// address is already in the transaction body.
    ///
    /// Returns `Ok(Some(addr))` when [`TxEip8130::sender`] is `None`: parses
    /// the 65-byte `r || s || v` ECDSA payload in [`Self::sender_auth`] and
    /// recovers the signer against [`TxEip8130::sender_signature_hash`].
    ///
    /// Returns `Err(_)` when the EOA payload is malformed (wrong length or
    /// invalid signature). Callers should treat a missing sender + malformed
    /// `sender_auth` as a hard rejection.
    #[cfg(feature = "k256")]
    pub fn recover_eoa_sender(
        &self,
    ) -> Result<Option<Address>, alloy_consensus::crypto::RecoveryError> {
        self.recover_eoa_sender_with(alloy_consensus::crypto::secp256k1::recover_signer)
    }
```

**File:** crates/common/precompiles/src/b20_asset/logic/v2.rs (L698-717)
```rust
    fn permit(
        &self,
        token: &mut B20AssetToken<S, A>,
        chain_id: u64,
        now: U256,
        args: PermitArgs,
    ) -> Result<()> {
        if now > args.deadline {
            return Err(BasePrecompileError::revert(IB20::ExpiredSignature {
                deadline: args.deadline,
            }));
        }
        let domain_sep = self.domain_separator(token, chain_id)?;
        let nonce = token.accounting().nonce(args.owner)?;
        let signing_hash = args.metered_signing_hash(token.accounting(), domain_sep, nonce)?;
        let recovered = args.metered_recover_signer(token.accounting(), signing_hash)?;
        PermitArgs::validate_recovered_address(recovered, args.owner)?;
        token.accounting_mut().increment_nonce(args.owner)?;
        self.approve(token, args.owner, args.spender, args.value)
    }
```

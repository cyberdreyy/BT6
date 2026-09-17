## Title
ECDSA Signature Malleability in B20 `permit` Signer Recovery — Missing EIP-2 Low-`s` Check - ([File: crates/common/precompiles/src/common/ops/permit.rs])

### Summary
The B20 token precompile's EIP-2612-style `permit` implementation recovers the signer via `PermitArgs::recover_signer`, which builds a raw signature with `alloy_primitives::Signature::from_scalars_and_parity(self.r, self.s, odd_y_parity)` and calls `recover_address_from_prehash` directly, with no check that `s` is in the canonical lower half of the secp256k1 curve order. [1](#0-0)  This is the same signature-malleability class described in the external report against the OpenZeppelin `ECDSA.recover` used pre-4.7.3.

### Finding Description
Elsewhere in this same codebase, every other secp256k1/P-256 signature-recovery path explicitly rejects malleable high-`s` signatures via `normalize_s()`/`sig.normalize_s().is_some()` checks:
- `RecoveredActorId::recover_k1` (the enshrined EIP-8130 k1 authenticator) explicitly rejects high-`s`. [2](#0-1) 
- `AuthenticatorDispatch::p256_verify` explicitly rejects high-`s` P-256 signatures "to match OpenZeppelin P256.verify (malleability check)". [3](#0-2) 
- The EOA transaction sender recovery path (`recover_eoa_sender`) is explicitly the "checked (EIP-2 low-s)" path, contrasted with a separate `_unchecked` variant reserved for a different contract. [4](#0-3) 

`PermitArgs::recover_signer`, used by both the V1 and V2 B20 asset `permit` logic, has no such guard. [5](#0-4) [6](#0-5)  Given any valid signature `(r, s, v)` over the EIP-712 `Permit` digest, the pair `(r, N - s, v')` (with `v'` the flipped recovery id, `N` the curve order) recovers to the identical `owner` address and will pass `validate_recovered_address`. [7](#0-6) 

### Impact Explanation
Since the on-chain nonce is embedded in the EIP-712 digest and incremented on success, exact replay is not possible, but the malleable second signature is a distinct valid witness for the *same* pending `permit` call. An attacker monitoring the mempool can extract `(r, s, v)` from a legitimate signer's pending `permit` transaction, derive the malleable counterpart, and submit it first. Because `permit`'s `signing_hash` is derived from the nonce read fresh at execution time, the front-run consumes the nonce; when the original transaction is later included, its digest recomputed against the now-incremented nonce no longer matches the original signature, so it reverts with `InvalidSigner`, griefing the original submitter and letting the attacker control the exact block/ordering in which the approval is applied. This is the classic permit front-running/DoS pattern enabled by ECDSA malleability, matching the "Medium" severity of the original report (mitigated only by the fact that funds cannot be directly stolen since `owner`/`spender`/`value` are still bound in the hash).

### Likelihood Explanation
Any unprivileged party observing a pending `permit` transaction in the public mempool (calldata is not encrypted) can trivially compute the malleable counterpart signature off-chain and resubmit it with higher gas/priority — no special access or privileged role required, matching the "depositor"/"B20 token creator" reachable-actor class explicitly permitted by the scan rules.

### Recommendation
Add an explicit EIP-2 canonical-`s` check in `PermitArgs::recover_signer` before recovery, mirroring the pattern already used in `RecoveredActorId::recover_k1` and `AuthenticatorDispatch::p256_verify` — reject (rather than normalize) any signature whose `s` is in the upper half of the curve order.

### Proof of Concept
1. Signer produces a valid `(r, s, v)` over `PermitArgs::signing_hash(domain_sep, nonce)` for `permit(owner, spender, value, deadline, v, r, s)` and broadcasts the transaction.
2. Attacker observes the pending transaction, computes `s' = N - s` and flips `v` (`27↔28`), producing `(r, s', v')`.
3. Attacker submits `permit(owner, spender, value, deadline, v', r, s')` with higher gas price; `PermitArgs::recover_signer` (no low-s check) recovers the same `owner`, `validate_recovered_address` passes, and the nonce is incremented. [8](#0-7) 
4. Original transaction, once mined, recomputes `signing_hash` against the now-bumped nonce, no longer matches the signature, and reverts — demonstrating the attacker fully controlled inclusion of the approval and griefed the legitimate sender's transaction.

### Citations

**File:** crates/common/precompiles/src/common/ops/permit.rs (L73-85)
```rust
    /// Validates a recovered ECDSA address against the declared `owner`.
    ///
    /// Returns `Err(InvalidSigner)` when `recovered` is `Address::ZERO` (matching Solidity's
    /// explicit zero-address guard) or when `recovered != owner`.
    pub fn validate_recovered_address(recovered: Address, owner: Address) -> Result<()> {
        if recovered.is_zero() || recovered != owner {
            return Err(BasePrecompileError::revert(IB20::InvalidSigner {
                signer: recovered,
                owner,
            }));
        }
        Ok(())
    }
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

**File:** crates/common/precompiles/src/common/ops/permit.rs (L619-625)
```rust

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

**File:** crates/execution/eip8130/src/dispatch.rs (L131-154)
```rust
    /// Verify a P-256 signature `(r, s)` over `prehash` for public key `(x, y)`.
    /// Enforces low-`s` to match `OpenZeppelin` `P256.verify` (malleability check).
    fn p256_verify(
        prehash: &[u8],
        r: &[u8],
        s: &[u8],
        x: &[u8],
        y: &[u8],
    ) -> Result<(), AuthError> {
        let mut sec1 = [0u8; 65];
        sec1[0] = 0x04;
        sec1[1..33].copy_from_slice(x);
        sec1[33..65].copy_from_slice(y);
        let key =
            P256VerifyingKey::from_sec1_bytes(&sec1).map_err(|_| AuthError::InvalidPublicKey)?;

        let mut rs = [0u8; 64];
        rs[..32].copy_from_slice(r);
        rs[32..].copy_from_slice(s);
        let signature = P256Signature::from_slice(&rs).map_err(|_| AuthError::InvalidSignature)?;
        if signature.normalize_s().is_some() {
            return Err(AuthError::InvalidSignature);
        }
        key.verify_prehash(prehash, &signature).map_err(|_| AuthError::InvalidSignature)
```

**File:** crates/common/consensus/src/transaction/eip8130/signed.rs (L888-924)
```rust
    #[cfg(feature = "k256")]
    #[test]
    fn recover_eoa_sender_unchecked_accepts_high_s_signature() {
        use alloy_primitives::U256;
        use alloy_signer::SignerSync;
        use alloy_signer_local::PrivateKeySigner;

        // secp256k1 curve order N.
        const SECP256K1_N: U256 = U256::from_be_slice(&[
            0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
            0xff, 0xfe, 0xba, 0xae, 0xdc, 0xe6, 0xaf, 0x48, 0xa0, 0x3b, 0xbf, 0xd2, 0x5e, 0x8c,
            0xd0, 0x36, 0x41, 0x41,
        ]);

        let signer = PrivateKeySigner::random();
        let expected = signer.address();

        let mut tx = sample_signed(false).into_tx();
        tx.sender = None;
        let hash = tx.sender_signature_hash();

        // Sign normally (low-s, EIP-2 canonical), then flip s into the upper half
        // by replacing it with N - s and inverting parity.
        let canonical = signer.sign_hash_sync(&hash).unwrap();
        let high_s_sig = alloy_primitives::Signature::new(
            canonical.r(),
            SECP256K1_N - canonical.s(),
            !canonical.v(),
        );
        let signed =
            Eip8130Signed::new(tx, Bytes::from(high_s_sig.as_bytes().to_vec()), Bytes::new());

        // The checked recovery rejects the high-s form (EIP-2);
        // the unchecked recovery accepts it and recovers the same address.
        assert!(signed.recover_eoa_sender().is_err());
        assert_eq!(signed.recover_eoa_sender_unchecked().unwrap(), Some(expected));
    }
```

**File:** crates/common/precompiles/src/b20_asset/logic/v1.rs (L607-626)
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
        let signing_hash = args.signing_hash(domain_sep, nonce);
        let recovered = args.recover_signer(signing_hash)?;
        PermitArgs::validate_recovered_address(recovered, args.owner)?;
        token.accounting_mut().increment_nonce(args.owner)?;
        self.approve(token, args.owner, args.spender, args.value)
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

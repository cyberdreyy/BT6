### Title
EIP-8130 transactions trigger expensive P-256/WebAuthn/delegate signature verification before the intrinsic-gas sufficiency check, enabling cheap unauthenticated CPU-exhaustion DoS - (File: `crates/execution/txpool/src/validator.rs`, `crates/execution/eip8130/src/dispatch.rs`)

### Summary
Both txpool admission and block execution of EIP-8130 (account-abstraction) transactions run the full authenticator verification pipeline — including secp256r1 (P-256) ECDSA verification and, for `WebAuthn`, two SHA-256 hashes plus a P-256 verify, or a nested "delegate" re-authentication — *before* checking whether the transaction's declared `gas_limit` is even large enough to cover the fixed authenticator gas cost. This mirrors the SEDA finding: an expensive, metered operation is performed unconditionally ahead of the check that gates it, letting an attacker force the expensive work while the transaction is ultimately rejected for insufficient gas and never pays anything.

### Finding Description
`BaseTransactionValidator::validate_eip8130_full` (mempool admission, reachable from any anonymous `eth_sendRawTransaction` caller) first runs `TransactionAuthorizer::authorize_and_apply`, which performs the actual cryptographic authentication of the sender/payer/config-change actors via `ActorTxVerifier`/`AuthenticatorDispatch`, and only afterward computes and checks the EIP-8130 intrinsic gas budget: [1](#0-0) 

The intrinsic-gas check happens later, well after the authentication work has already completed: [2](#0-1) 

The same ordering exists in block execution's `Eip8130Executor::authorize_and_apply`, where step "1. Authorize and apply" (including full authenticator dispatch) runs before step "5. Intrinsic gas": [3](#0-2) [4](#0-3) 

The authenticator dispatch itself performs non-trivial cryptography unconditionally, regardless of whether the caller supplied enough gas to pay for it: [5](#0-4) [6](#0-5) 

The EIP-8130 gas schedule documents these authenticator costs as fixed, precompile-equivalent prices meant to *bound* this work — P-256 at 6,900 gas, `WebAuthn` at 6,900 gas, delegate at `2,100 + nested`: [7](#0-6) 

Critically, the worst-case intrinsic gas for a transaction can be computed *without* running authentication at all — `IntrinsicGas::compute`/`IntrinsicGasInput::worst_case` only need the signed transaction body and cheap structural flags, not the result of `authorize_and_apply`: [8](#0-7) 

Because the cheap structural pre-check (`validate_eip8130_structural`) only rejects a `gas_limit == 0`, and never checks `gas_limit` against even a coarse worst-case intrinsic-gas floor for the declared authenticator scheme, a transaction can be crafted with a `gas_limit` deliberately set just below what a P-256/`WebAuthn`/delegate authentication actually costs. Such a transaction is cheap to construct (no valid signature, valid balance, or valid nonce is required to reach the authenticator dispatch), forces the full expensive verification to run, and is only rejected afterward at the intrinsic-gas check.

### Impact Explanation
Any anonymous RPC client can submit a stream of EIP-8130 transactions with malformed/garbage but correctly-sized `WebAuthn` or P-256 authenticator payloads and an under-provisioned `gas_limit`. Each submission forces the node to perform secp256r1 ECDSA verification (not hardware-accelerated like secp256k1) and, for `WebAuthn`, two additional SHA-256 hashes, at admission time in every node's mempool and again during block execution if ever included — all for free, since the transaction is rejected and never pays a fee or consumes pool space long-term. Because this same validation path runs both in mempool admission (on every node accepting the transaction) and block-building/execution, a sustained stream of such transactions is a genuine CPU-exhaustion DoS vector against block production and RPC-serving nodes, directly analogous to the SEDA Tally VM startup-cost bug: expensive work performed before the gas-sufficiency gate that should have prevented it.

### Likelihood Explanation
The attack requires no balance, no valid signature, and no special privilege — only an anonymous transaction submission via the public RPC (`eth_sendRawTransaction`) or p2p transaction gossip acceptance path is needed. The malformed payload only needs correct byte-length/ABI framing to reach the actual cryptographic verification routines (`p256_verify`, `webauthn`), which is trivial to construct offline.

### Recommendation
Before invoking `TransactionAuthorizer::authorize_and_apply` (and before `AuthenticatorDispatch::authenticate`) in both `validate_eip8130_full` and `Eip8130Executor::authorize_and_apply`, compute a cheap worst-case intrinsic-gas estimate from the declared authenticator selector(s) (`sender_auth`/`payer_auth` prefix, config-change authenticator addresses) and reject the transaction immediately with `GasTooLow` if `tx.gas_limit` cannot cover it — mirroring the SEDA fix of gating expensive VM/authenticator startup on a pre-computed gas floor rather than charging/validating gas only after the work is done.

### Proof of Concept
1. Build an `Eip8130Signed` transaction with an explicit configured sender whose `sender_auth` is `WEBAUTHN_AUTHENTICATOR || garbage-but-correctly-framed WebAuthnAuth blob` (or a `DELEGATE_AUTHENTICATOR` blob nesting another `WebAuthn`/P-256 leaf for extra cost), and set `tx.gas_limit` just below the value `IntrinsicGas::compute` would require for that authenticator.
2. Submit repeatedly via `eth_sendRawTransaction` from many throwaway/zero-balance senders (no valid signature or funds required).
3. Observe that each submission causes `validate_eip8130_full` → `TransactionAuthorizer::authorize_and_apply` → `AuthenticatorDispatch::webauthn`/`p256` to run full SHA-256 + secp256r1 verification (see `crates/execution/eip8130/src/dispatch.rs:160-219`) before being rejected at the `intrinsic.execution_gas_available(...)` check (`crates/execution/txpool/src/validator.rs:1117`), demonstrating unmetered expensive work ahead of the gas gate.

### Citations

**File:** crates/execution/txpool/src/validator.rs (L1016-1047)
```rust
        let auth_start = Instant::now();
        let auth_result = StorageCtx::enter(&mut storage, |ctx| {
            let applied = {
                let mut account_config = AccountConfigurationStorage::new(ctx);
                TransactionAuthorizer::authorize_and_apply(
                    signed,
                    &mut account_config,
                    local_chain_id,
                    now,
                )?
            };
            if let Some(delegation) = applied.applied.delegation {
                delegation.install(ctx).map_err(TxAuthError::from)?;
            }

            let sender = applied.actors.sender.account;
            let payer = applied.actors.payer.map_or(sender, |actor| actor.account);
            // Thread authoritative applied/actor data through rather than
            // re-scanning account changes or re-resolving actors below.
            let is_create = applied.applied.created.is_some();
            Ok::<_, TxAuthError>((
                sender,
                payer,
                applied.actors.sender.resolved,
                is_create,
                applied.actors.payer.map(|actor| actor.resolved),
            ))
        });
        ValidatorMetrics::auth_seconds(Self::sender_sig_type(signed))
            .record(auth_start.elapsed().as_secs_f64());
        let (sender, payer, sender_actor, is_create, payer_actor) =
            auth_result.map_err(Self::map_tx_auth_error)?;
```

**File:** crates/execution/txpool/src/validator.rs (L1099-1119)
```rust
        let sender_auto_delegated =
            IntrinsicGasInput::sender_auto_delegated(&signed.tx().account_changes);
        let encoded = self.eip8130_encoded(signed);
        // Admission uses the same safe ceiling as `eth_estimateGas`, so a tx whose
        // `gas_limit` was set from the estimate is never rejected here and can
        // never be admitted only to OOG at inclusion. The non-monotonic,
        // state-dependent costs are pinned to their worst case: both policy gates
        // charged and zero revoke discount. Execution reprices them precisely.
        let intrinsic = IntrinsicGas::compute(
            signed,
            encoded.as_ref(),
            &IntrinsicGasInput::worst_case(
                nonce_key_first_use,
                sender_auto_delegated,
                signed.tx().payer.is_some(),
            ),
        )
        .map_err(|_| Self::eip8130_error("intrinsic gas computation failed"))?;
        if intrinsic.execution_gas_available(signed.tx().gas_limit).is_none() {
            return Err(InvalidTransactionError::GasTooLow.into());
        }
```

**File:** crates/common/evm/src/eip8130.rs (L971-1017)
```rust
        StorageCtx::enter(&mut provider, |sctx| {
            // Ordering note: the apply step (1) and code effects (2) write journal
            // storage *before* the nonce is validated (3). Any `Err` returned from
            // this closure propagates out of `authorize_and_apply` and the caller
            // discards the transaction, so these earlier writes never persist for a
            // rejected transaction. This mirrors the caller-MUST-discard contract
            // documented on `TransactionAuthorizer::authorize_and_apply`.
            let mut acc = AccountConfigurationStorage::new(sctx);

            // 1. Authorize and apply the account changes interleaved against the
            //    evolving state, then authenticate sender/payer against the
            //    resulting post-apply state. `AccountConfiguration` storage
            //    transitions are written here; the deferred account-code effects
            //    are installed in step 2.
            let applied_tx =
                TransactionAuthorizer::authorize_and_apply(signed, &mut acc, chain_id, now)
                    .map_err(BaseTransactionError::eip8130)?;
            let has_explicit_delegation = applied_tx.applied.delegation.is_some();
            let sender_actor = applied_tx.actors.sender.resolved;
            let payer_policy_gated = applied_tx
                .actors
                .payer
                .as_ref()
                .is_some_and(|actor| actor.resolved.is_policy_gated());
            let sender = applied_tx.actors.sender.account;
            let payer = applied_tx.actors.payer.as_ref().map_or(sender, |p| p.account);
            // Defense-in-depth: `authorize_and_apply` -> `verify_sender` already
            // gates `can_use_nonce_key(nonce_key)` on both the configured and
            // EOA sender paths, so this is redundant on the current call graph. It
            // is kept as a local guard so this execution entry point stays sound if
            // the sender-resolution path is ever refactored to skip that check.
            if !sender_actor.can_use_nonce_key(nonce_key) {
                return Err(BaseTransactionError::eip8130(
                    "sender actor scope does not authorize sequenced nonces",
                ));
            }

            // 2. Install the deferred account-*code* effects (created-account
            //    bytecode, delegation indicator) the apply step surfaced.
            if let Some(created) = &applied_tx.applied.created {
                Self::install_created_code(sctx, created.address, &created.code)?;
            }
            if let Some(delegation) = &applied_tx.applied.delegation {
                delegation.install(sctx).map_err(BaseTransactionError::eip8130)?;
            }

            // 3. Validate and advance the nonce.
```

**File:** crates/common/evm/src/eip8130.rs (L1082-1090)
```rust
            // 5. Intrinsic gas under the EIP-8130 schedule.
            let (sender_intrinsic, payer_auth, execution_gas_available) =
                Self::resolve_execution_gas(
                    signed,
                    encoded,
                    &IntrinsicGasInput::new(nonce_key_first_use, sender_auto_delegated)
                        .with_policy_gates(sender_actor.is_policy_gated(), payer_policy_gated)
                        .with_revoke_discount_slots(applied_tx.revoke_discount_slots),
                    gas_limit,
```

**File:** crates/execution/eip8130/src/dispatch.rs (L122-155)
```rust
    fn p256(hash: B256, data: &[u8]) -> Result<B256, AuthError> {
        if data.len() != 129 {
            return Err(AuthError::MalformedAuth);
        }
        let (r, s, x, y) = (&data[0..32], &data[32..64], &data[64..96], &data[96..128]);
        Self::p256_verify(hash.as_slice(), r, s, x, y)?;
        Ok(keccak256([x, y].concat()))
    }

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
    }
```

**File:** crates/execution/eip8130/src/dispatch.rs (L160-219)
```rust
    fn webauthn(hash: B256, data: &[u8]) -> Result<B256, AuthError> {
        let decoded = <(WebAuthnAuth, B256, B256)>::abi_decode_params(data)
            .map_err(|_| AuthError::MalformedAuth)?;

        // Canonical-encoding guard. `data` is un-signed, relay-mutable material —
        // the WebAuthn signature commits to the challenge and `clientDataJSON`,
        // not to this outer ABI framing — yet every byte is billed as EIP-2028
        // payload gas. A non-canonical re-encoding (trailing padding or
        // non-minimal dynamic offsets) decodes to the same value and still passes
        // the P-256 check, but changes the transaction hash and inflates
        // sender-intrinsic gas, letting a relayer shrink the execution-gas budget
        // without the key. Requiring the input to equal its canonical ABI
        // re-encoding makes any such mutation invalidate the transaction. Honest
        // producers (Solidity `abi.encode` / `alloy` `abi_encode_params`) always
        // emit this canonical form, so this rejects only malleated blobs.
        if decoded.abi_encode_params() != data {
            return Err(AuthError::MalformedAuth);
        }
        let (auth, x, y) = decoded;

        let auth_data = auth.authenticatorData.as_ref();
        let client_json = auth.clientDataJSON.as_bytes();

        // 37-byte minimum authenticator data (32 rpIdHash + 1 flags + 4 counter).
        if auth_data.len() <= 36 {
            return Err(AuthError::InvalidSignature);
        }
        // Step 11: `"type":"webauthn.get"` at typeIndex.
        Self::contains_at(client_json, &auth.typeIndex, WEBAUTHN_TYPE)?;
        // Step 12: `"challenge":"<base64url(hash)>"` at challengeIndex.
        let mut expected = Vec::with_capacity(WEBAUTHN_CHALLENGE_PREFIX.len() + 44);
        expected.extend_from_slice(WEBAUTHN_CHALLENGE_PREFIX);
        expected.extend_from_slice(URL_SAFE_NO_PAD.encode(hash.as_slice()).as_bytes());
        expected.push(b'"');
        Self::contains_at(client_json, &auth.challengeIndex, &expected)?;
        // Step 16: User Present bit must be set. Step 17 (UV) skipped (requireUV = false).
        let flags = auth_data[32];
        if flags & FLAG_USER_PRESENT != FLAG_USER_PRESENT {
            return Err(AuthError::InvalidSignature);
        }
        // Backup state consistency: BS=1 requires BE=1.
        if flags & FLAG_BACKUP_ELIGIBLE != FLAG_BACKUP_ELIGIBLE && flags & FLAG_BACKUP_STATE != 0 {
            return Err(AuthError::InvalidSignature);
        }
        // Step 19-20: P-256 verify over sha256(authenticatorData || sha256(clientDataJSON)).
        let client_hash = Sha256::digest(client_json);
        let mut signed = Sha256::new();
        signed.update(auth_data);
        signed.update(client_hash);
        let signed = signed.finalize();

        Self::p256_verify(
            signed.as_slice(),
            auth.r.as_slice(),
            auth.s.as_slice(),
            x.as_slice(),
            y.as_slice(),
        )?;
        Ok(keccak256([x.as_slice(), y.as_slice()].concat()))
    }
```

**File:** crates/execution/eip8130/README.md (L95-100)
```markdown
| Authenticator | execution gas | basis |
|---|---|---|
| secp256k1 (`K1_AUTHENTICATOR` sentinel, EOA path) | 3,000 | `ECRECOVER` precompile |
| P-256 | 6,900 | EIP-7951 `P256VERIFY` precompile |
| `WebAuthn` | 6,900 | P-256 verify + SHA-256 + `clientDataJSON` handling |
| delegate (depth-1) | `2,100 + nested` | extra cold `actor_config` SLOAD on the delegate account + the nested authenticator's execution |
```

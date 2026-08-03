Based on the actual code, this scenario does not hold up as a valid finding.

**Native ECDSA recovery implementation:** `native_ecdsa_recover` in `aptos-move/framework/natives/src/cryptography/secp256k1.rs:27-87` uses the well-audited `libsecp256k1` crate directly: it parses the message via `Message::parse_slice`, the recovery id via `RecoveryId::parse`, and the signature via `Signature::parse_standard_slice` — the latter enforces canonical (low-S) form, which specifically rejects malleable high-S signatures rather than accepting them. [1](#0-0) 

The actual recovery call, `libsecp256k1::recover(&msg, &sig, &rid)`, returns `Ok(pk)` only when the signature cryptographically verifies against the recovered public key for that exact message. It does not "forge" arbitrary public keys — recovering a public key that verifies a given signature is inherent to ECDSA recovery, not a bug, and producing a signature that recovers to a *specific victim's* Ethereum address requires knowledge of that address's private key. [2](#0-1) 

**Downstream verification closes the gap anyway:** even setting aside the native's correctness, `ethereum_derivable_account.move`'s `authenticate_auth_data` recomputes the Ethereum address from the recovered public key via Keccak256 and asserts it matches the claimed account address (`EADDR_MISMATCH`), so a mismatched/incorrect recovery would abort rather than silently authenticate as an arbitrary address.
<invoke name="codebase_search">
<parameter name="query">placeholder</parameter>
</invoke>

### Citations

**File:** aptos-move/framework/natives/src/cryptography/secp256k1.rs (L44-86)
```rust
    let msg = match libsecp256k1::Message::parse_slice(&msg) {
        Ok(msg) => msg,
        Err(_) => {
            return Err(SafeNativeError::abort_with_message(
                abort_codes::NFE_DESERIALIZE,
                "Message must be exactly 32 bytes",
            ));
        },
    };

    // NOTE(Gas): O(1) cost
    let rid = match libsecp256k1::RecoveryId::parse(recovery_id) {
        Ok(rid) => rid,
        Err(_) => {
            return Err(SafeNativeError::abort_with_message(
                abort_codes::NFE_DESERIALIZE,
                "Recovery ID must be 0, 1, 2, or 3",
            ));
        },
    };

    // NOTE(Gas): O(1) deserialization cost
    // which seems to be 64 bytes, so O(1) cost for all intents and purposes.
    let sig = match libsecp256k1::Signature::parse_standard_slice(&signature) {
        Ok(sig) => sig,
        Err(_) => {
            return Err(SafeNativeError::abort_with_message(
                abort_codes::NFE_DESERIALIZE,
                "Signature must be exactly 64 bytes",
            ));
        },
    };

    context.charge(SECP256K1_ECDSA_RECOVER * NumArgs::one())?;

    // NOTE(Gas): O(1) cost: a size-2 multi-scalar multiplication
    match libsecp256k1::recover(&msg, &sig, &rid) {
        Ok(pk) => Ok(smallvec![
            Value::vector_u8(pk.serialize()[1..].to_vec()),
            Value::bool(true)
        ]),
        Err(_) => Ok(smallvec![Value::vector_u8([0u8; 0]), Value::bool(false)]),
    }
```

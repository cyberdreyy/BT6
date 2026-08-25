### Title
Secp256k1 Signature Malleability in `sol_secp256k1_recover` Syscall / Secp256k1 Precompile Enables Bypass of Program-Level Replay Protection - (`syscalls/src/lib.rs`, `precompiles/src/secp256k1.rs`)

### Summary
Agave's secp256k1 recovery primitives — the `SyscallSecp256k1Recover` syscall exposed to on-chain programs and the standalone `Secp256k1SigVerify` precompile program — perform ECDSA public-key recovery via `libsecp256k1::recover` without enforcing signature canonicality (low-S / single recovery-id). Both accept a signature and its "flipped" (S = -S, recovery_id XOR 1) malleable counterpart as equally valid for the same message and public key. Any deployed Solana program that (like the referenced `RunaAGI.sol`) implements its own signature-based replay/nonce protection on top of this primitive — e.g., storing the raw signature bytes as a "used" marker instead of a message hash or explicit nonce — inherits the exact malleability bypass described in the external report.

### Finding Description
The syscall implementation recovers the public key straight from caller-supplied `signature`/`recovery_id` bytes with no canonical-form check: [1](#0-0) 

The equivalent precompile verification routine used by the runtime exhibits the same behavior: [2](#0-1) 

This is explicitly demonstrated (and treated as expected behavior, not a bug to fix) by the precompile's own test suite, which signs a message, flips the `S` value and recovery id to produce an alternate but equally-valid signature for the same message/pubkey, and asserts that verification of *both* signatures succeeds: [3](#0-2) 

The SBF program test for the `sol_secp256k1_recover` syscall likewise documents that "secp256k1_recover allows malleable signatures," recovering the identical public key from both the original and the S-flipped signature: [4](#0-3) 

By contrast, Agave's ed25519 precompile was hardened against this exact class of bug: it unconditionally calls `verify_strict` (which rejects non-canonical/malleable signatures), and the `_feature_set` parameter is no longer even consulted, meaning strict verification is always enforced: [5](#0-4) [6](#0-5) 

No analogous hardening exists for secp256k1. Since `sol_secp256k1_recover` and the secp256k1 native program are directly reachable by any ordinary user's deployed BPF program (this is precisely the primitive Ethereum-bridging/whitelist/pre-sale-style Solana programs use to verify off-chain-signed messages, mirroring `ECDSA.recover` in the reported Solidity contract), any Solana program that marks a signature itself (rather than the message hash or an explicit nonce account) as "already used" for replay protection can be bypassed: an attacker takes a previously-observed valid `(signature, recovery_id)`, computes the low-S/flipped-recovery-id alternate encoding, and resubmits it. `libsecp256k1::recover` returns the identical public key, so the program's signer check passes, while the program's naive "signature already used" bookkeeping (keyed on raw signature bytes) does not recognize it as a duplicate.

### Impact Explanation
This is the direct on-chain-program analog of the audited `RunaAGI.sol` finding: programs built on Agave's secp256k1 syscall/precompile that use the raw signature (rather than the recovered pubkey + message hash + an explicit nonce) as their replay-protection key can have a whitelist mint, claim, airdrop, or similar one-time-use signed authorization replayed multiple times, leading to unauthorized state mutation / fund drain (e.g., double minting, double claiming) in any program that follows this common but unsafe pattern. The severity is scoped to programs built atop this primitive; it does not itself corrupt validator consensus, but it is a concrete unauthorized-state-mutation vector reachable purely through normal transaction submission against a deployed program, with no privileged access required.

### Likelihood Explanation
Likelihood is Medium: exploitation requires a specific (but common, as shown by the external report) program design pattern — using the secp256k1-recovered signature bytes themselves for replay protection rather than a message hash/nonce. Agave provides no built-in protection against malleable secp256k1 signatures at the syscall/precompile layer (unlike ed25519, which is hardened), so any program author following naive tutorials/patterns for "verify Ethereum signature + mark used" inherits the vulnerability without any special validator configuration. An attacker only needs to observe one valid on-chain signature (all Solana transactions/instruction data are public) and perform a standard elliptic-curve S-negation to forge the resubmittable variant.

### Recommendation
Provide a hardened alternative (or make it the default) for `sol_secp256k1_recover` / the secp256k1 precompile that enforces canonical low-S and low recovery-id signatures, analogous to the `ed25519_precompile_verify_strict` treatment already applied unconditionally to the ed25519 precompile. At minimum, update documentation for `sol_secp256k1_recover` (`programs/sbf/c/inc/sol/secp256k1.h`, `precompiles/src/secp256k1.rs`) to explicitly and prominently warn program developers that recovered signatures are malleable and must never be used as a replay-protection key — only the recovered public key plus an explicit nonce/message hash should be used for that purpose.

### Proof of Concept
The malleability is already demonstrated in-repo and requires no new PoC code; it can be reproduced directly from the existing test: [7](#0-6) 

Steps to exploit against a hypothetical vulnerable program (mirroring `RunaAGI.sol`'s pattern):
1. Off-chain authority signs message `M` for user `U`, producing `(sig, recovery_id)`.
2. `U` submits transaction invoking the vulnerable program's `claim()` instruction with `secp256k1_recover(hash(M), recovery_id, sig)`, which recovers the authority's pubkey; program records `sig` bytes as "used" and pays out.
3. Attacker (or `U` again) computes `alt_sig = negate_S(sig)`, `alt_recovery_id = recovery_id ^ 1` — this is exactly the transform in the cited test.
4. Attacker resubmits `claim()` with `(alt_sig, alt_recovery_id)` for the same message `M`. `secp256k1_recover`/the precompile verifies successfully and recovers the same authority pubkey, but the program's "used signature" set does not contain `alt_sig`, so the claim is processed a second time.

### Citations

**File:** syscalls/src/lib.rs (L958-969)
```rust
        let Ok(recovery_id) = libsecp256k1::RecoveryId::parse(adjusted_recover_id_val) else {
            return Ok(Secp256k1RecoverError::InvalidRecoveryId.into());
        };
        let Ok(signature) = libsecp256k1::Signature::parse_standard_slice(signature) else {
            return Ok(Secp256k1RecoverError::InvalidSignature.into());
        };
        let public_key = match libsecp256k1::recover(&message, &signature, &recovery_id) {
            Ok(key) => key.serialize(),
            Err(_) => {
                return Ok(Secp256k1RecoverError::InvalidSignature.into());
            }
        };
```

**File:** precompiles/src/secp256k1.rs (L65-100)
```rust
        let signature = libsecp256k1::Signature::parse_standard_slice(
            &signature_instruction[sig_start..sig_end],
        )
        .map_err(|_| PrecompileError::InvalidSignature)?;

        let recovery_id = libsecp256k1::RecoveryId::parse(signature_instruction[sig_end])
            .map_err(|_| PrecompileError::InvalidRecoveryId)?;

        // Parse out pubkey
        let eth_address_slice = get_data_slice(
            instruction_datas,
            offsets.eth_address_instruction_index,
            offsets.eth_address_offset,
            HASHED_PUBKEY_SERIALIZED_SIZE,
        )?;

        // Parse out message
        let message_slice = get_data_slice(
            instruction_datas,
            offsets.message_instruction_index,
            offsets.message_data_offset,
            offsets.message_data_size as usize,
        )?;

        let message_hash: [u8; 32] = solana_keccak_hasher::hash(message_slice).to_bytes();
        let pubkey = libsecp256k1::recover(
            &libsecp256k1::Message::parse_slice(&message_hash).unwrap(),
            &signature,
            &recovery_id,
        )
        .map_err(|_| PrecompileError::InvalidSignature)?;
        let eth_address = eth_address_from_pubkey(&pubkey.serialize()[1..].try_into().unwrap());

        if eth_address_slice != eth_address {
            return Err(PrecompileError::InvalidSignature);
        }
```

**File:** precompiles/src/secp256k1.rs (L343-413)
```rust
    // Signatures are malleable.
    #[test]
    fn test_malleability() {
        agave_logger::setup();

        let secret_bytes: [u8; 32] = rand::random();
        let secret_key = libsecp256k1::SecretKey::parse(&secret_bytes).unwrap();
        let public_key = libsecp256k1::PublicKey::from_secret_key(&secret_key);
        let eth_address = eth_address_from_pubkey(&public_key.serialize()[1..].try_into().unwrap());

        let message = b"hello";
        let message_hash = {
            let mut hasher = keccak::Hasher::default();
            hasher.hash(message);
            hasher.result()
        };

        let secp_message = libsecp256k1::Message::parse(message_hash.as_bytes());
        let (signature, recovery_id) = libsecp256k1::sign(&secp_message, &secret_key);

        // Flip the S value in the signature to make a different but valid signature.
        let mut alt_signature = signature;
        alt_signature.s = -alt_signature.s;
        let alt_recovery_id = libsecp256k1::RecoveryId::parse(recovery_id.serialize() ^ 1).unwrap();

        let mut data: Vec<u8> = vec![];
        let mut both_offsets = vec![];

        // Verify both signatures of the same message.
        let sigs = [(signature, recovery_id), (alt_signature, alt_recovery_id)];
        for (signature, recovery_id) in sigs.iter() {
            let signature_offset = data.len();
            data.extend(signature.serialize());
            data.push(recovery_id.serialize());
            let eth_address_offset = data.len();
            data.extend(eth_address);
            let message_data_offset = data.len();
            data.extend(message);

            let data_start = 1 + SIGNATURE_OFFSETS_SERIALIZED_SIZE * 2;

            let offsets = SecpSignatureOffsets {
                signature_offset: (signature_offset + data_start) as u16,
                signature_instruction_index: 0,
                eth_address_offset: (eth_address_offset + data_start) as u16,
                eth_address_instruction_index: 0,
                message_data_offset: (message_data_offset + data_start) as u16,
                message_data_size: message.len() as u16,
                message_instruction_index: 0,
            };

            both_offsets.push(offsets);
        }

        let mut instruction_data: Vec<u8> = vec![2];

        for offsets in both_offsets {
            let offsets = bincode::serialize(&offsets).unwrap();
            instruction_data.extend(offsets);
        }

        instruction_data.extend(data);

        test_verify_with_alignment(
            verify,
            &instruction_data,
            &[&instruction_data],
            &FeatureSet::all_enabled(),
        )
        .unwrap();
    }
```

**File:** programs/sbf/rust/secp256k1_recover/src/lib.rs (L37-83)
```rust
/// secp256k1_recover allows malleable signatures
fn test_secp256k1_recover_malleability() {
    // hash of the string "hello world"
    let message_hash = solana_hash::Hash::new_from_array([
        0x47, 0x17, 0x32, 0x85, 0xa8, 0xd7, 0x34, 0x1e, 0x5e, 0x97, 0x2f, 0xc6, 0x77, 0x28, 0x63,
        0x84, 0xf8, 0x02, 0xf8, 0xef, 0x42, 0xa5, 0xec, 0x5f, 0x03, 0xbb, 0xfa, 0x25, 0x4c, 0xb0,
        0x1f, 0xad,
    ]);

    let pubkey_bytes: [u8; 64] = [
        0x9B, 0xEE, 0x7C, 0x18, 0x34, 0xE0, 0x18, 0x21, 0x7B, 0x40, 0x14, 0x9B, 0x84, 0x2E, 0xFA,
        0x80, 0x96, 0x00, 0x1A, 0x9B, 0x17, 0x88, 0x01, 0x80, 0xA8, 0x46, 0x99, 0x09, 0xE9, 0xC4,
        0x73, 0x6E, 0x39, 0x0B, 0x94, 0x00, 0x97, 0x68, 0xC2, 0x28, 0xB5, 0x55, 0xD3, 0x0C, 0x0C,
        0x42, 0x43, 0xC1, 0xEE, 0xA5, 0x0D, 0xC0, 0x48, 0x62, 0xD3, 0xAE, 0xB0, 0x3D, 0xA2, 0x20,
        0xAC, 0x11, 0x85, 0xEE,
    ];
    let signature_bytes: [u8; 64] = [
        0x93, 0x92, 0xC4, 0x6C, 0x42, 0xF6, 0x31, 0x73, 0x81, 0xD4, 0xB2, 0x44, 0xE9, 0x2F, 0xFC,
        0xE3, 0xF4, 0x57, 0xDD, 0x50, 0xB3, 0xA5, 0x20, 0x26, 0x3B, 0xE7, 0xEF, 0x8A, 0xB0, 0x69,
        0xBB, 0xDE, 0x2F, 0x90, 0x12, 0x93, 0xD7, 0x3F, 0xA0, 0x29, 0x0C, 0x46, 0x4B, 0x97, 0xC5,
        0x00, 0xAD, 0xEA, 0x6A, 0x64, 0x4D, 0xC3, 0x8D, 0x25, 0x24, 0xEF, 0x97, 0x6D, 0xC6, 0xD7,
        0x1D, 0x9F, 0x5A, 0x26,
    ];
    let recovery_id: u8 = 0;

    let signature = libsecp256k1::Signature::parse_standard_slice(&signature_bytes).unwrap();

    // Flip the S value in the signature to make a different but valid signature.
    let mut alt_signature = signature;
    alt_signature.s = -alt_signature.s;
    let alt_recovery_id = libsecp256k1::RecoveryId::parse(recovery_id ^ 1).unwrap();

    let alt_signature_bytes = alt_signature.serialize();
    let alt_recovery_id = alt_recovery_id.serialize();

    let recovered_pubkey =
        secp256k1_recover(message_hash.as_bytes(), recovery_id, &signature_bytes[..]).unwrap();
    assert_eq!(recovered_pubkey.to_bytes(), pubkey_bytes);

    let alt_recovered_pubkey = secp256k1_recover(
        message_hash.as_bytes(),
        alt_recovery_id,
        &alt_signature_bytes[..],
    )
    .unwrap();
    assert_eq!(alt_recovered_pubkey.to_bytes(), pubkey_bytes);
}
```

**File:** precompiles/src/ed25519.rs (L11-76)
```rust
pub fn verify(
    data: &[u8],
    instruction_datas: &[&[u8]],
    _feature_set: &FeatureSet,
) -> Result<(), PrecompileError> {
    if data.len() < SIGNATURE_OFFSETS_START {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    let num_signatures = data[0] as usize;
    if num_signatures == 0 && data.len() > SIGNATURE_OFFSETS_START {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    let expected_data_size = num_signatures
        .saturating_mul(SIGNATURE_OFFSETS_SERIALIZED_SIZE)
        .saturating_add(SIGNATURE_OFFSETS_START);
    // We do not check or use the byte at data[1]
    if data.len() < expected_data_size {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    for i in 0..num_signatures {
        let start = i
            .saturating_mul(SIGNATURE_OFFSETS_SERIALIZED_SIZE)
            .saturating_add(SIGNATURE_OFFSETS_START);

        // SAFETY:
        // - data[start..] is guaranteed to be >= size of Ed25519SignatureOffsets
        // - Ed25519SignatureOffsets is a POD type, so we can safely read it as an unaligned struct
        let offsets = unsafe {
            core::ptr::read_unaligned(data.as_ptr().add(start) as *const Ed25519SignatureOffsets)
        };

        // Parse out signature
        let signature = get_data_slice(
            data,
            instruction_datas,
            offsets.signature_instruction_index,
            offsets.signature_offset,
            SIGNATURE_SERIALIZED_SIZE,
        )?;

        let signature =
            Signature::from_bytes(signature).map_err(|_| PrecompileError::InvalidSignature)?;

        // Parse out pubkey
        let pubkey = get_data_slice(
            data,
            instruction_datas,
            offsets.public_key_instruction_index,
            offsets.public_key_offset,
            PUBKEY_SERIALIZED_SIZE,
        )?;

        let publickey = ed25519_dalek::PublicKey::from_bytes(pubkey)
            .map_err(|_| PrecompileError::InvalidPublicKey)?;

        // Parse out message
        let message = get_data_slice(
            data,
            instruction_datas,
            offsets.message_instruction_index,
            offsets.message_data_offset,
            offsets.message_data_size as usize,
        )?;
        publickey
            .verify_strict(message, &signature)
            .map_err(|_| PrecompileError::InvalidSignature)?;
```

**File:** precompiles/src/ed25519.rs (L454-512)
```rust
    #[test]
    fn test_ed25519_malleability() {
        agave_logger::setup();

        // sig created via ed25519_dalek: both pass
        let secret_bytes: [u8; 32] = rand::random();
        let secret = ed25519_dalek::SecretKey::from_bytes(&secret_bytes).unwrap();
        let public: ed25519_dalek::PublicKey = (&secret).into();
        let privkey = ed25519_dalek::Keypair { secret, public };
        let message_arr = b"hello";
        let signature = privkey.sign(message_arr).to_bytes();
        let pubkey = privkey.public.to_bytes();
        let instruction = new_ed25519_instruction_with_signature(message_arr, &signature, &pubkey);

        let feature_set = FeatureSet::default();
        assert!(
            test_verify_with_alignment(
                verify,
                &instruction.data,
                &[&instruction.data],
                &feature_set
            )
            .is_ok()
        );

        let feature_set = FeatureSet::all_enabled();
        assert!(
            test_verify_with_alignment(
                verify,
                &instruction.data,
                &[&instruction.data],
                &feature_set
            )
            .is_ok()
        );

        // malleable sig: verify_strict does NOT pass
        // for example, test number 5:
        // https://github.com/C2SP/CCTV/tree/main/ed25519
        // R has low order (in fact R == 0)
        let pubkey =
            &hex::decode("10eb7c3acfb2bed3e0d6ab89bf5a3d6afddd1176ce4812e38d9fd485058fdb1f")
                .unwrap();
        let signature = &hex::decode("00000000000000000000000000000000000000000000000000000000000000009472a69cd9a701a50d130ed52189e2455b23767db52cacb8716fb896ffeeac09").unwrap();
        let message = b"ed25519vectors 3";
        let instruction = new_ed25519_instruction_raw(pubkey, signature, message);

        // verify_strict does NOT pass for malleable signature
        let feature_set = FeatureSet::default();
        assert!(
            test_verify_with_alignment(
                verify,
                &instruction.data,
                &[&instruction.data],
                &feature_set
            )
            .is_err()
        );
    }
```

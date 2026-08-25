### Title
Ed25519/Secp256k1/Secp256r1 precompiles verify signatures over caller-selected instruction data without binding the signed message to the invoking program or instruction context - (File: precompiles/src/ed25519.rs, precompiles/src/secp256k1.rs, precompiles/src/secp256r1.rs)

### Summary
The Agave precompile programs (`ed25519`, `secp256k1`, `secp256r1`) verify a signature over a byte-range that is selected purely by numeric `instruction_index`/`offset` fields supplied inside the precompile instruction's own data. Nothing in the verified payload or in the precompile's `verify()` routine binds the checked signature to the specific downstream program/instruction that intends to rely on it. This is structurally identical to the ParaSpace `Credit` struct bug: a signed blob lacking a context/domain identifier lets a signature that was produced for one purpose be pointed at by a *different* consumer within the same composed transaction, as long as the raw bytes match.

### Finding Description
`verify()` in `precompiles/src/ed25519.rs` resolves the signed `message` via `get_data_slice`, which accepts an arbitrary `message_instruction_index` and indexes into `instruction_datas` (i.e., any instruction in the whole transaction, or the precompile's own data when the index is `u16::MAX`): [1](#0-0) [2](#0-1) 

The same pattern exists in `precompiles/src/secp256k1.rs`: [3](#0-2) 

and in `precompiles/src/secp256r1.rs`: [4](#0-3) 

These `verify()` functions are invoked generically per-transaction by `verify_if_precompile`, which just forwards all instruction datas from the whole transaction to whichever precompile matches the `program_id`: [5](#0-4) 

The precompile only proves "some keypair signed exactly these bytes." It carries no notion of which program, instruction, or business context (e.g., an order ID, a marketplace address, a nonce) the signer intended the signature to authorize — exactly the missing-domain-separator flaw described in the ParaSpace report, where the `Credit` struct lacked a `MarketplaceAddress` field binding the signature to a specific marketplace/order. Any on-chain program that relies on the precompile's success to authorize an action (e.g., mint/transfer/exchange logic) is fully responsible for independently re-validating that the referenced `instruction_index`/offsets point at *its own* expected instruction and byte layout; if that validation is incomplete (e.g., only checks that `verify()` returned `Ok`, or checks index but not exact offsets/instruction ordering), an attacker can compose a transaction that reuses a legitimately-produced signature/message pair — supplied via any other instruction in the same transaction — to satisfy a completely unrelated program's authorization check.

### Impact Explanation
If a deployed program's authorization logic trusts precompile success without strictly re-validating that the message offsets/instruction index correspond to its own instruction and expected payload layout, an attacker can splice a victim's previously-produced (or otherwise obtainable) signature over one payload into a transaction context where it is misinterpreted as authorizing a different, attacker-chosen action — directly mirroring the ParaSpace outcome (fund loss via unintended state mutation, since the "message" has no domain separator forcing it to a single legitimate use). Because the check happens at the core runtime level, it affects any and every program built on these precompiles, not one marketplace.

### Likelihood Explanation
Likelihood is contingent on how a specific downstream program consumes the precompile result — the precompile itself functions as documented. This mirrors the original report's own downgrade rationale (impact would be HIGH but is downgraded because the enabling "supporting code" is currently only partially present/depends on external program design). It requires a program author to omit the recommended offset/instruction-index binding checks, which is a known, easy-to-miss integration pitfall for ed25519/secp256k1/secp256r1 precompile consumers on Solana/Agave.

### Recommendation
- Document (and where possible enforce via helper libraries shipped with `solana-ed25519-program`/`solana-secp256k1-program`/`solana-secp256r1-program`) that any consuming program MUST verify that `message_instruction_index` equals the index of its own instruction (not `u16::MAX` unless intended) and that `message_data_offset`/`message_data_size` exactly match the expected serialized layout of its own instruction data.
- Consider adding an optional "domain" or "consumer program id" field to the offsets structures (analogous to the suggested `MarketplaceAddress` fix) so a program can assert the signed message was scoped to it specifically, rather than relying purely on byte-range equality.
- Provide a safe, audited helper (e.g., `verify_offsets_belong_to_instruction`) in `solana-precompile-error`/`agave-precompiles` that downstream programs can call to enforce correct binding, reducing the chance of ad hoc, incomplete validation.

### Proof of Concept
1. Program A expects the ed25519 precompile instruction immediately preceding it in the transaction to sign message `M_A = serialize(A_specific_fields)`, and merely checks that the preceding instruction is the ed25519 program and that `verify_if_precompile` succeeded (i.e., does not check `message_instruction_index`/offsets strictly against its own instruction's data).
2. An attacker builds a transaction containing: (i) an ed25519 precompile instruction whose `message_instruction_index` points at instruction #k's data (some unrelated instruction present anyway, or the victim's own previously-published signed payload data included verbatim in the tx), and (ii) Program A's instruction.
3. `precompiles::lib::verify_if_precompile` → `ed25519::verify` (`precompiles/src/ed25519.rs:11-79`) succeeds because the raw bytes at the referenced offset match a legitimately produced signature, even though semantically they were never intended to authorize Program A's action.
4. Program A's under-validated check passes, and it executes the attacker's chosen action as if authorized by the victim's signature — the on-chain analog of ParaSpace's credit-signature replay across marketplaces/order IDs.

### Citations

**File:** precompiles/src/ed25519.rs (L66-79)
```rust
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
    }
    Ok(())
}
```

**File:** precompiles/src/ed25519.rs (L81-105)
```rust
fn get_data_slice<'a>(
    data: &'a [u8],
    instruction_datas: &'a [&[u8]],
    instruction_index: u16,
    offset_start: u16,
    size: usize,
) -> Result<&'a [u8], PrecompileError> {
    let instruction = if instruction_index == u16::MAX {
        data
    } else {
        let signature_index = instruction_index as usize;
        if signature_index >= instruction_datas.len() {
            return Err(PrecompileError::InvalidDataOffsets);
        }
        instruction_datas[signature_index]
    };

    let start = offset_start as usize;
    let end = start.saturating_add(size);
    if end > instruction.len() {
        return Err(PrecompileError::InvalidDataOffsets);
    }

    Ok(&instruction[start..end])
}
```

**File:** precompiles/src/secp256k1.rs (L73-103)
```rust
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
    }
    Ok(())
}
```

**File:** precompiles/src/secp256r1.rs (L80-97)
```rust
        // Parse out pubkey
        let pubkey = get_data_slice(
            data,
            instruction_datas,
            offsets.public_key_instruction_index,
            offsets.public_key_offset,
            COMPRESSED_PUBKEY_SERIALIZED_SIZE,
        )?;

        // Parse out message
        let message = get_data_slice(
            data,
            instruction_datas,
            offsets.message_instruction_index,
            offsets.message_data_offset,
            offsets.message_data_size as usize,
        )?;

```

**File:** precompiles/src/lib.rs (L98-119)
```rust
/// Check that a program is precompiled and if so verify it
pub fn verify_if_precompile(
    program_id: &Pubkey,
    precompile_instruction: &CompiledInstruction,
    all_instructions: &[CompiledInstruction],
    feature_set: &FeatureSet,
) -> Result<(), PrecompileError> {
    for precompile in PRECOMPILES.iter() {
        if precompile.check_id(program_id, |feature_id| feature_set.is_active(feature_id)) {
            let instruction_datas: Vec<_> = all_instructions
                .iter()
                .map(|instruction| instruction.data.as_ref())
                .collect();
            return precompile.verify(
                &precompile_instruction.data,
                &instruction_datas,
                feature_set,
            );
        }
    }
    Ok(())
}
```

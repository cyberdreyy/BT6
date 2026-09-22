### Title
Precompile signature verification trivially succeeds with zero signatures (empty-credential authentication bypass analog) - ([File: precompiles/src/secp256k1.rs], [File: precompiles/src/ed25519.rs])

### Summary
The `secp256k1` and `ed25519` precompile `verify()` functions accept a "zero signatures" instruction as successfully verified when the instruction data is minimal, instead of unconditionally rejecting it. This is structurally identical to CVE-2014-0097: an authentication routine that fails to reject an empty/degenerate credential, causing the verification step to trivially "pass" without actually checking anything.

### Finding Description
`agave_precompiles::secp256k1::verify` only rejects `count == 0` when there is *extra* trailing data beyond the header byte: [1](#0-0) 

If `data` is exactly one byte (`[0]`), `count == 0` and `data.len() == 1`, so the `count == 0 && data.len() > 1` guard does not fire. Execution falls through to `for i in 0..count { ... }`, which iterates zero times, and the function returns `Ok(())` — i.e., the precompile instruction is reported as "verified" despite verifying zero signatures.

The `ed25519` precompile has the exact same pattern: [2](#0-1) 

Here `SIGNATURE_OFFSETS_START` is the minimal header size; if `data.len() == SIGNATURE_OFFSETS_START` and `num_signatures == 0`, the guard `data.len() > SIGNATURE_OFFSETS_START` is false, so no error is returned, and the empty-loop again yields `Ok(())`.

By contrast, `secp256r1::verify` shows what the *correct* fix looks like — it unconditionally rejects `num_signatures == 0`: [3](#0-2) 

These `verify_fn`s are exactly what gets invoked whenever a transaction includes a `Secp256k1Program`/`Ed25519Program` instruction, via `Precompile::verify` / `verify_if_precompile` and `Bank::process_precompile` (`InvokeContextCallback::process_precompile`), which every validator runs independently as part of normal transaction processing: [4](#0-3) [5](#0-4) 

This is reachable directly from an unprivileged transaction sender: any account can submit a transaction that includes a `Secp256k1Program`/`Ed25519Program` instruction with `count = 0` and minimal (1–2 byte) instruction data, and that instruction will unconditionally succeed on every validator, deterministically, with zero actual cryptographic verification performed — the on-chain equivalent of Spring Security's ActiveDirectory authenticator accepting an empty password because it never checked the credential length before deciding "authenticated."

### Impact Explanation
The precompile mechanism exists specifically so that on-chain programs (bridges, oracles, multisig/guardian schemes, off-chain attestation consumers, etc.) can trust that "if a `Secp256k1Program`/`Ed25519Program` instruction appears earlier in the transaction and the transaction didn't fail, N real signatures over specific message bytes were checked." Because `count == 0` with minimal data is accepted as `Ok(())` instead of being rejected, an attacker can craft a transaction containing a zero-signature precompile instruction that "verifies" trivially, potentially satisfying downstream program logic that assumes a non-zero, checked signature set is present without separately re-validating `num_signatures`. This is a CPI/authorization-privilege-escalation-class bug: a program's second-order trust in `verify_if_precompile`'s success is broken by a degenerate input that should have failed sanitization but doesn't.

### Likelihood Explanation
High reachability, since it requires nothing more than constructing one instruction with a single (or two) zero bytes as data inside an otherwise ordinary, self-signed transaction — no special privileges, no cooperating validator/leader behavior, and it is deterministic across all validators (both `secp256k1::verify` and `ed25519::verify` are pure functions of instruction data). The barrier to actual damage is whether a downstream consumer program checks `num_signatures` itself; but the precompile's own contract ("verify signatures or fail") is violated for the degenerate zero-signature case, which is exactly the failure mode the `count == 0 && data.len() > 1` and `num_signatures == 0 && data.len() > SIGNATURE_OFFSETS_START` checks were clearly *intended* to catch but do not cover completely.

### Recommendation
Change both `secp256k1::verify` and `ed25519::verify` to unconditionally reject `count == 0` / `num_signatures == 0`, matching the stricter and correct pattern already used in `secp256r1::verify` (`precompiles/src/secp256r1.rs` lines 26-29), rather than only rejecting it when trailing data happens to be present.

### Proof of Concept
1. Construct a transaction that includes an instruction with `program_id = solana_sdk_ids::secp256k1_program::id()` and `data = vec![0u8]` (a single zero byte, i.e., `count = 0`, no offsets/signature entries).
2. Submit the transaction to any Agave validator.
3. Observe that `agave_precompiles::secp256k1::verify` returns `Ok(())` for this instruction (verified through `Precompile::verify` and `Bank::process_precompile`), i.e., the instruction succeeds despite zero signatures being checked — reproducible directly from the code shown in `precompiles/src/secp256k1.rs` lines 28-37 (and analogously `precompiles/src/ed25519.rs` lines 16-22 with `data = vec![0u8, 0u8]`).

### Citations

**File:** precompiles/src/secp256k1.rs (L28-37)
```rust
    if data.is_empty() {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    let count = data[0] as usize;
    if count == 0 && data.len() > 1 {
        // count is zero but the instruction data indicates that is probably not
        // correct, fail the instruction to catch probable invalid secp256k1
        // instruction construction.
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
```

**File:** precompiles/src/ed25519.rs (L16-22)
```rust
    if data.len() < SIGNATURE_OFFSETS_START {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
    let num_signatures = data[0] as usize;
    if num_signatures == 0 && data.len() > SIGNATURE_OFFSETS_START {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
```

**File:** precompiles/src/secp256r1.rs (L26-29)
```rust
    let num_signatures = data[0] as usize;
    if num_signatures == 0 {
        return Err(PrecompileError::InvalidInstructionDataSize);
    }
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

**File:** runtime/src/bank.rs (L6694-6707)
```rust
    fn process_precompile(
        &self,
        program_id: &Pubkey,
        data: &[u8],
        instruction_datas: Vec<&[u8]>,
    ) -> std::result::Result<(), PrecompileError> {
        if let Some(precompile) = get_precompile(program_id, |feature_id: &Pubkey| {
            self.feature_set.is_active(feature_id)
        }) {
            precompile.verify(data, &instruction_datas, &self.feature_set)
        } else {
            Err(PrecompileError::InvalidPublicKey)
        }
    }
```

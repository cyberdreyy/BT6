### Title
Attacker-controlled range-program verification key ("sub image id") lets a prover forge aggregate ZK proofs with an arbitrary output root - ([File: crates/proof/zk/programs/succinct/aggregation/src/main.rs])

### Summary
The aggregation zkVM program verifies each "range" sub-proof using a verification key (`multi_block_vkey`) that is read directly from untrusted proof input (`AggregationInputs`), not a value hardcoded/pinned inside the aggregation guest program. That same attacker-controlled key is then re-emitted as the `imageHash` field of the committed proof journal. This is structurally the same class of bug as the referenced Taiko fix: "add sub image id into ZK aggregation proof verification" — i.e., a missing binding of the sub-circuit's image/verification identity inside the aggregation program's verification logic.

### Finding Description
In the SP1 aggregation guest program, sub-proofs from the "range" program are verified like this: [1](#0-0) 

The verifying key used here, `agg_inputs.multi_block_vkey`, comes straight from the `AggregationInputs` struct that is written to the zkVM's stdin by the (untrusted, off-chain) witness-generation code: [2](#0-1) 

The stdin encoder writes this vkey verbatim from whatever `SP1VerifyingKey` is passed by the caller, with no guest-side assertion that it equals a fixed, protocol-pinned range-program vkey: [3](#0-2) 

The aggregation program then converts this same attacker-suppliable vkey into the `imageHash` field of its committed output/journal: [4](#0-3) 

Because the guest program itself never asserts `agg_inputs.multi_block_vkey == EXPECTED_RANGE_VKEY`, the "identity" of the sub-circuit being verified (its image id/vkey) is not bound by the aggregation circuit's own logic — exactly the missing check the Taiko commit added ("add sub image id into ZK aggregation proof verification"). All correctness for this binding is pushed off-chain/on-chain to whoever eventually reads `imageHash` from the journal (e.g., a legacy `OPSuccinctL2OutputOracle.opSuccinctConfigs` mapping with `rangeVkeyCommitment`), but the `AggregateVerifier` dispute-game bindings inspected in this repository do not surface an equivalent on-chain range-vkey pinning check — only PLONK/journal digest verification via a fixed *aggregation* vkey was found: [5](#0-4) [6](#0-5) 

The only vkey that is actually pinned/verified externally is the aggregation program's own vkey (`agg_vk`, checked via `cluster_setup_vkeys()`), not the range program's vkey embedded inside the journal it produces.

**Note on limits of this analysis**: This finding is based on the Rust source visible to the indexer. The Solidity `AggregateVerifier` contract itself (which is what ultimately decodes `imageHash`/`multi_block_vkey` from the proof journal and would be the place a range-vkey pin *should* live) is not present in this repository's index — only Rust FFI bindings to it (`sol!` macros) were found. It is possible the actual on-chain contract independently hardcodes and checks the range vkey against `imageHash`, in which case this specific path is not exploitable end-to-end. This could not be confirmed or ruled out from the available files, so a Devin session with full repository/contract access is recommended to verify whether `imageHash` (i.e., the range-program vkey) is checked on-chain against a fixed expected value before a submitted PLONK proof is accepted.

### Impact Explanation
If the on-chain verifier does not independently pin the expected range-program vkey and instead trusts the `imageHash` value embedded in the aggregation proof's own journal, an attacker (a prover/dispute-game participant with only a signed L1 transaction) could:
1. Write a completely different SP1 "range" program (with no real derivation logic — e.g., one that accepts any output root as valid).
2. Set `AggregationInputs.multi_block_vkey` to that malicious program's vkey.
3. Generate a real PLONK aggregation proof (the aggregation circuit's *own* identity/vkey is unaffected — only the sub-proof it verifies is swapped), which will pass `sp1_lib::verify::verify_sp1_proof` because the vkey supplied matches the malicious sub-proof.
4. Submit `AggregateVerifier.verifyProposalProof(proof)` with an attacker-chosen, false `l2PostRoot`/output root and have it accepted as valid.

This would allow submission of a wrong, unbacked L2 output root, defeating the dispute-game's fraud-proof guarantee and enabling theft of funds via fraudulent withdrawal proofs — a Critical impact class matching the report's "wrong provable output root" criterion.

### Likelihood Explanation
Likelihood depends entirely on whether the on-chain `AggregateVerifier`/dispute-game contract independently re-derives or hardcodes the expected range-program vkey and rejects proofs whose `imageHash` does not match it. That check is not visible anywhere in the Rust codebase indexed here (only aggregation-vkey pinning was found), so it is uncertain whether this is a live vulnerability or a defense-in-depth gap already mitigated on the Solidity side.

### Recommendation
- Add an explicit assertion inside the aggregation zkVM guest program (`crates/proof/zk/programs/succinct/aggregation/src/main.rs`) that `agg_inputs.multi_block_vkey` equals a compile-time-pinned constant for the expected range-program vkey, rather than trusting the untrusted stdin value.
- Independently, confirm (via the Solidity `AggregateVerifier` contract, not visible in this index) that `imageHash` decoded from the aggregation proof journal is checked against a protocol-governed, immutable expected range vkey before `verifyProposalProof`/`challenge`/`nullify` accept the proof.
- Recommend starting a Devin session with full access to the `base-succinct`/contracts repository to audit the Solidity-side handling of `imageHash` in `AggregateVerifier` and confirm or refute exploitability.

### Proof of Concept
Not independently reproducible from this repository alone because the on-chain Solidity contract that ultimately consumes `imageHash` is not available in the index. Conceptually: build and register an alternate "range" SP1 program that outputs an attacker-chosen `BootInfoStruct` without doing real derivation; supply its vkey as `multi_block_vkey` in `AggregationInputs`; run it through `get_agg_proof_stdin` / the aggregation program; obtain a valid PLONK proof; submit via `AggregateVerifier.verifyProposalProof`. Whether the transaction reverts depends on unseen on-chain logic.

### Citations

**File:** crates/proof/zk/programs/succinct/aggregation/src/main.rs (L45-53)
```rust
    // Verify each range program proof.
    agg_inputs.boot_infos.iter().for_each(|boot_info| {
        // In the range program, the public values digest is just the hash of the ABI encoded
        // boot info.
        let serialized_boot_info = bincode::serialize(&boot_info).unwrap();
        let pv_digest = Sha256::digest(serialized_boot_info);

        sp1_lib::verify::verify_sp1_proof(&agg_inputs.multi_block_vkey, &pv_digest.into());
    });
```

**File:** crates/proof/zk/programs/succinct/aggregation/src/main.rs (L100-115)
```rust

    // Convert the range vkey to a B256.
    let multi_block_vkey_b256 = B256::from(u32_to_u8(agg_inputs.multi_block_vkey));

    let agg_outputs = AggregationOutputs {
        proverAddress: agg_inputs.prover_address,
        l1Head: final_boot_info.l1Head,
        l2PreRoot: final_boot_info.l2PreRoot,
        startingL2SequenceNumber: final_boot_info.l2PreBlockNumber,
        l2PostRoot: final_boot_info.l2PostRoot,
        endingL2SequenceNumber: final_boot_info.l2BlockNumber,
        intermediateRoots: final_boot_info.intermediateRoots,
        rollupConfigHash: final_boot_info.rollupConfigHash,
        imageHash: multi_block_vkey_b256,
        scheduleId: final_boot_info.scheduleId,
    };
```

**File:** crates/proof/zk/utils/src/types.rs (L7-18)
```rust
/// Inputs to the aggregation program.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AggregationInputs {
    /// Per-range boot info structs.
    pub boot_infos: Vec<BootInfoStruct>,
    /// L1 block hash anchoring all ranges.
    pub latest_l1_checkpoint_head: B256,
    /// Verification key for the range program.
    pub multi_block_vkey: [u32; 8],
    /// On-chain address of the prover.
    pub prover_address: Address,
}
```

**File:** crates/proof/zk/backend/src/succinct/utils/stdin.rs (L24-46)
```rust
/// Build the SP1 stdin for the aggregation proof from range proofs and headers.
pub fn get_agg_proof_stdin(
    proofs: Vec<SP1Proof>,
    boot_infos: Vec<BootInfoStruct>,
    headers: Vec<Header>,
    multi_block_vkey: &sp1_sdk::SP1VerifyingKey,
    latest_checkpoint_head: B256,
    prover_address: Address,
) -> Result<SP1Stdin> {
    let mut stdin = SP1Stdin::default();
    for proof in proofs {
        let SP1Proof::Compressed(compressed_proof) = proof else {
            return Err(anyhow::anyhow!("Invalid proof passed as compressed proof!"));
        };
        stdin.write_proof(*compressed_proof, multi_block_vkey.vk.clone());
    }

    stdin.write(&AggregationInputs {
        boot_infos,
        latest_l1_checkpoint_head: latest_checkpoint_head,
        multi_block_vkey: multi_block_vkey.hash_u32(),
        prover_address,
    });
```

**File:** crates/infra/snark-e2e/src/snark_e2e.rs (L344-355)
```rust
        // -- 5. Compute aggregation verifying key ---------------------------------
        info!("computing aggregation verifying key (LightProver — VK only)");
        let t = std::time::Instant::now();
        let (_range_vk, agg_vk) = base_proof_zk_backend::cluster_setup_vkeys()
            .await
            .context("failed to compute aggregation verifying key")?;
        info!(elapsed_secs = t.elapsed().as_secs_f64(), "aggregation verifying key computed");

        // -- 6. Verify SNARK proof ------------------------------------------------
        Self::verify_snark_proof(snark_proof, agg_vk)
            .await
            .with_context(|| format!("failed to verify SNARK proof for session_id={session_id}"))?;
```

**File:** crates/proof/contracts/src/aggregate_verifier.rs (L133-134)
```rust
        /// Verifies an additional proof for the current game.
        function verifyProposalProof(bytes calldata proofBytes) external;
```

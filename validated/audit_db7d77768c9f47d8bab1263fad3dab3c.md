Based on the investigation, I found a genuine analog worth flagging, but I could not fully verify exploitability due to running out of tool iterations before reading `crates/common/precompiles/src/b20_factory/variant.rs` and `crates/common/precompiles/src/b20_factory/abi/mod.rs` (the read_file calls failed on my final attempt).

### Title
Potential node panic on malformed `createB20`/`getB20Address` calldata via unvalidated enum discriminant decode - ([File: crates/common/precompiles/src/b20_factory/dispatch.rs])

### Summary
The `B20Factory` precompile's `route` function unwraps `B20Variant::from_abi(call.variant)` with `.expect(...)`, relying entirely on the assumption that `abi_decode_validate` upstream always rejects out-of-range enum discriminants before this code runs. The author's own comment in `versions.rs` explicitly flags this as a landmine: *"dispatch unwraps `B20Variant::from_abi` on the decoded call with `expect`, so a non-validating decode here would turn an out-of-range variant byte from a revert into a node panic."* This is structurally the same bug class as CVE-2023-3774: an unhandled error path in a "resource creation" operation (Vault namespace creation / here, B-20 token creation) that can crash the process instead of returning a controlled error.

### Finding Description [1](#0-0) 
`route()` calls `version.abi().decode(calldata)?` and then unconditionally `.expect()`s that `B20Variant::from_abi` succeeds for both the `createB20` and `getB20Address` arms: [2](#0-1) 

The safety of this `.expect()` depends entirely on `FactoryAbi::abi_decode_validate` (in `versions.rs`) always using alloy's *validating* decoder rather than a non-validating one: [3](#0-2) 

The author's own doc comment on `abi_decode_validate` acknowledges the exact failure mode: a non-validating decode here would turn a malformed/out-of-range `variant` byte from a normal revert into a node panic [4](#0-3) .

This is precisely the CVE-2023-3774 bug class: an unhandled error in an object-creation code path (Vault Enterprise namespace creation / here, B20 token factory `createB20`) that, if the validating invariant is ever violated (by a future `FactoryAbi` version, a refactor that swaps to a non-validating decode, or an unaccounted decode path), turns attacker-supplied calldata into a guaranteed process panic reachable by any unprivileged transaction sender or `eth_call`/precompile caller.

### Impact Explanation
If the invariant that `abi_decode_validate` (rather than a non-validating decode) is used ever breaks — which the code explicitly warns is a live risk for future versions — any account can send a transaction (or issue an RPC `eth_call`) to the `B20Factory` precompile with calldata for `createB20` or `getB20Address` carrying an out-of-range `variant` enum byte. This would panic the EVM execution thread inside block execution, crashing the sequencer/validator node process — a denial-of-service that halts block production and/or crashes RPC-serving nodes, matching the "Critical: production panics or unchecked assumptions in block paths" scenario explicitly called out in the repo's own review guide.

### Likelihood Explanation
Currently low/theoretical: the present `FactoryAbi::V1::abi_decode_validate` does call the validating decoder (`IB20FactoryV1::IB20FactoryCalls::abi_decode_validate`), so under current code the `.expect()` should not fire today. However, the correctness of a *reachable, attacker-controlled panic* rests on a single undocumented, non-type-enforced invariant across every future `FactoryAbi` version and any other call path into `route()`/`create_b20()`, exactly the "unhandled error" root cause pattern that caused BIT-vault-2023-3774. There is no defensive fallback (e.g., returning a revert if `from_abi` fails) — only a comment-based guarantee.

### Recommendation
Replace the `.expect()` calls in `route()` (lines 97-99 and 113-115 of `dispatch.rs`) with a defensive `Err` path (mirroring the pattern already used elsewhere in the same file, e.g. `InvalidVariant` revert in `logic/v1.rs`), so a future decode-path change or overlooked call site cannot turn malformed calldata into a node-crashing panic. Add a unit/fuzz test that decodes `createB20`/`getB20Address` calldata with an out-of-range variant discriminant directly through `route()` (bypassing the `abi_decode_validate` gate) to prove the panic can never be reached regardless of upstream decode choice.

### Proof of Concept
Not independently reproducible from the indexed context alone: I could not confirm whether any code path calls `B20FactoryStorage::route`/`create_b20` with calldata that has *not* passed through `FactoryAbi::abi_decode_validate` (e.g., via a hypothetical non-validating fast path, a future ABI version, or a cross-crate caller). The `.expect()` and its guarding invariant are both confirmed in the code; a concrete crash trigger would require identifying such a bypass, which I was unable to complete before running out of tool calls.

**Caveat:** Due to the final-iteration tool limit, I was unable to inspect `crates/common/precompiles/src/b20_factory/variant.rs` (defining `B20Variant::from_abi`) or `crates/common/precompiles/src/b20_factory/abi/mod.rs` to fully confirm there is no alternate route bypassing validation. If you need a definitive answer on exploitability, a Devin session with full file access should verify these two files and check for any additional callers of `B20FactoryStorage::route`/`create_b20` across the workspace.

### Citations

**File:** crates/common/precompiles/src/b20_factory/dispatch.rs (L91-99)
```rust
        let logic = version.implementation();
        match version.abi().decode(calldata)? {
            IB20Factory::IB20FactoryCalls::createB20(call) => {
                let caller = ctx.caller();
                // abi_decode_validate rejects non-canonical discriminants before dispatch,
                // so from_abi returning None here would be an internal invariant violation.
                let variant = B20Variant::from_abi(call.variant).expect(
                    "abi_decode_validate rejects non-canonical discriminants before dispatch",
                );
```

**File:** crates/common/precompiles/src/b20_factory/dispatch.rs (L112-118)
```rust
            IB20Factory::IB20FactoryCalls::getB20Address(call) => {
                let v = B20Variant::from_abi(call.variant).expect(
                    "abi_decode_validate rejects non-canonical discriminants before dispatch",
                );
                let hash = ctx.metered_keccak256(&(call.sender, call.salt).abi_encode())?;
                let addr = v.compute_address_from_hash(hash).0;
                Ok(IB20Factory::getB20AddressCall::abi_encode_returns(&addr).into())
```

**File:** crates/common/precompiles/src/b20_factory/versions.rs (L65-85)
```rust
    /// Decodes `calldata` against this wire surface, mirroring alloy's `abi_decode_validate` and
    /// mapping its failure onto [`BasePrecompileError::AbiDecodeFailed`].
    ///
    /// Always the *validating* decode. `dispatch` unwraps `B20Variant::from_abi` on the decoded
    /// call with `expect`, so a non-validating decode here would turn an out-of-range variant byte
    /// from a revert into a node panic.
    pub fn abi_decode_validate(
        self,
        calldata: &[u8],
        selector: [u8; 4],
    ) -> Result<IB20Factory::IB20FactoryCalls> {
        match self {
            // Canonical aliases the V1 surface, so the frozen decode already yields the canonical
            // call type. A later surface adds its own arm, re-decoding against canonical.
            Self::V1 => IB20FactoryV1::IB20FactoryCalls::abi_decode_validate(calldata),
        }
        .map_err(|error| BasePrecompileError::AbiDecodeFailed {
            selector,
            error: error.to_string(),
        })
    }
```

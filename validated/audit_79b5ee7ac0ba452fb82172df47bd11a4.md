### Title
Attacker-triggerable `BasePrecompileError::Fatal`/`SlotOverflow` in B-20 precompile storage converts ordinary user input into an unrecoverable EVM system error, halting block building and validator re-execution - (File: `crates/common/precompile-storage/src/error.rs`)

### Summary
The B-20 precompile storage layer classifies certain error conditions as "system errors" (`Fatal`, `Panic`, `OutOfGas`, `SlotOverflow`) that `is_system_error()` says "must be propagated rather than turned into a revert." [1](#0-0)  Several of these conditions are reachable purely by growing a user-controlled `Vec<T>` field (e.g. B-20 policy/announcement lists) to its packed-slot arithmetic limits through ordinary `push` calls, which return `BasePrecompileError::Fatal("Vec is at max capacity")` or `SlotOverflow` instead of a catchable revert. [2](#0-1)  This mirrors the dForce `_updateInterest` bug class: a value fully reachable by normal (even unprivileged) transactions crosses an internal safety check, but instead of a normal revert, it surfaces as a "must propagate" system error.

### Finding Description
`BasePrecompileError` distinguishes recoverable, ABI-encodable reverts from `is_system_error()` variants (`OutOfGas`, `Fatal`, `Panic`, `SlotOverflow`) that the codebase's own documentation says "propagate rather than turn into a revert" [1](#0-0) . `into_precompile_result` explicitly does not convert `SlotOverflow`/`Fatal` into an ABI revert — it returns `Err(PrecompileError::Fatal(..))` instead [3](#0-2) .

The project's own review guide names this exact bug class as Critical: "Precompile fatal execution semantics ... convert malformed, oversized, or otherwise user-controlled input from normal EVM halt/revert behavior into fatal `Err`, panic, or process exit behavior ... Sequencer can fail to build a block containing the transaction; validators can get stuck re-executing a block that contains it." [4](#0-3) 

A concrete reachable trigger: any dynamic `Vec<T>` field backing B-20 precompile storage (e.g. announcement/allowlist-style lists used by B-20 tokens per `crates/common/precompiles/README.md`) uses `VecHandler::push`, which returns `Fatal("Vec is at max capacity")` once `length >= max_index()` [2](#0-1) . `max_index()` is bounded by `u32::MAX / T::BYTES` (or `/T::SLOTS`) — for `T::BYTES <= 16` (e.g. packed booleans/ids) this can be a large but attacker-reachable count via repeated legitimate calls that append entries, with no privileged gate preventing growth. Once reached, every subsequent call into any code path that touches that Vec (`push`, and any `at`/`try_compute_handler` slot computation that could overflow `checked_add`) returns a `Fatal`/`SlotOverflow` error rather than a normal ABI revert, and per `is_system_error()` this is treated as a value that "must be propagated" through the EVM rather than swallowed as a transaction revert.

### Impact Explanation
Per `is_system_error()`'s own contract and the `BLOCK_PRODUCTION_REVIEW.md` taxonomy, when a precompile call surfaces `PrecompileError::Fatal` instead of a revert/halt, this becomes a non-invalid-tx EVM execution error. In the builder, this can abort payload/flashblock construction (`PayloadBuilderError::EvmExecutionError`); in block execution/validation, the same input reproducibly causes the same fatal error for every node re-executing the block, since the storage state (Vec length at its cap) is now permanent chain state that any future transaction touching that same storage slot will hit again. This is not a per-transaction revert that simply excludes the offending transaction — it is a state-dependent condition baked into on-chain storage that deterministically re-triggers whenever the same code path executes, exactly analogous to `_updateInterest` reverting on every subsequent call once the interest-rate threshold is crossed on-chain. This satisfies "node halt" / consensus-liveness impact criteria (sequencer cannot build valid blocks touching the affected storage; validators/other nodes get stuck re-executing).

### Likelihood Explanation
Reaching `max_index()` (bounded by `u32::MAX` divided by element byte size) requires a very large number of `push` operations, which is realistic only over an extended period of legitimate/malicious usage rather than a single transaction — this bounds likelihood to Medium rather than High. However, no privileged role, precompile bug, or unusual gas budget is required: any account with standard permission to append to an affected B-20 Vec-backed field can, over time, drive the array to this boundary, at which point the fatal (non-revert) failure becomes permanent chain-state behavior for that storage slot.

### Recommendation
- Convert `Fatal("Vec is at max capacity")` and `SlotOverflow` in `crates/common/precompile-storage/src/types/vec.rs` (and any other type reachable from user-triggerable growth) into ordinary ABI-encoded reverts rather than `is_system_error()` classified `Fatal` errors, so the affected transaction/tx fails cleanly without corrupting builder/validator EVM execution semantics.
- Add an explicit application-level cap (well below the packed/slot arithmetic ceiling) enforced with a normal revert before storage operations reach the `checked_add`/`max_index()` boundary, so the condition is caught at "reasonable business logic" limits, not at the raw arithmetic limit.
- Add boundary tests (as required by `BLOCK_PRODUCTION_REVIEW.md`'s Mandatory Payload/Data Boundary Gate) exercising `push` exactly at and just above `max_index()` to confirm the failure mode is a clean revert, not a fatal/halt condition, for both the builder's tx-execution path and validator re-execution path.

### Proof of Concept
1. Deploy/target a B-20 token (or other precompile) whose storage layout includes a `Vec<T>` field with small `T::BYTES` (e.g. `Vec<bool>` or `Vec<u8>`-sized ids), reachable via a public precompile ABI method that internally calls `VecHandler::push`.
2. Repeatedly submit transactions invoking that method until the Vec's length reaches `VecHandler::<T>::max_index()` (`u32::MAX / T::BYTES`).
3. Submit one more transaction that calls `push` (or any operation triggering `try_compute_handler`'s `checked_add` at the boundary).
4. Observe that instead of a normal EVM revert, the call returns `BasePrecompileError::Fatal("Vec is at max capacity")` / `SlotOverflow`, which `into_precompile_result` does not ABI-encode into a revert [5](#0-4) , and which `is_system_error()` marks for propagation rather than being absorbed as an invalid-transaction outcome [1](#0-0) .
5. Any future transaction touching that same Vec storage will deterministically retrigger the same fatal path, per `BLOCK_PRODUCTION_REVIEW.md`'s documented "Sequencer can fail to build a block ... validators can get stuck re-executing" impact [4](#0-3) .

### Citations

**File:** crates/common/precompile-storage/src/error.rs (L75-79)
```rust
impl BasePrecompileError {
    /// Returns true if this error must be propagated rather than turned into a revert.
    pub const fn is_system_error(&self) -> bool {
        matches!(self, Self::OutOfGas | Self::Fatal(_) | Self::Panic(_) | Self::SlotOverflow)
    }
```

**File:** crates/common/precompile-storage/src/error.rs (L110-126)
```rust
    /// ABI-encodes this error and wraps it as a [`PrecompileResult`] (revert or fatal error).
    ///
    /// Internal dispatch diagnostics use compact, non-ABI revert data: unknown selectors return the
    /// raw selector bytes, and decode failures return `selector || utf8_error_string`.
    pub fn into_precompile_result(self, gas: u64, state_gas: u64) -> PrecompileResult {
        let bytes: Bytes = match self {
            Self::Revert(bytes) => bytes,
            Self::Panic(kind) => Panic { code: U256::from(kind as u32) }.abi_encode().into(),
            Self::OutOfGas => {
                return Ok(PrecompileOutput::halt(PrecompileHalt::OutOfGas, 0));
            }
            Self::SlotOverflow => {
                return Err(PrecompileError::Fatal("slot overflow".into()));
            }
            Self::Fatal(msg) => {
                return Err(PrecompileError::Fatal(msg));
            }
```

**File:** crates/common/precompile-storage/src/types/vec.rs (L238-254)
```rust
    /// Pushes a new element to the end of the vector.
    #[inline]
    pub fn push(&self, value: T) -> Result<()>
    where
        T: Storable,
        T::Handler<'a>: Handler<T>,
    {
        let length = self.len()?;
        if length >= Self::max_index() {
            return Err(BasePrecompileError::Fatal("Vec is at max capacity".into()));
        }
        let mut elem_slot =
            Self::try_compute_handler(self.data_slot(), self.address, self.storage, length)?;
        elem_slot.write(value)?;
        let mut length_slot = Slot::<U256>::new(self.len_slot, self.address, self.storage);
        length_slot.write(U256::from(length + 1))
    }
```

**File:** docs/guides/BLOCK_PRODUCTION_REVIEW.md (L29-29)
```markdown
| Precompile fatal execution semantics | BLS, BN254, P256, MODEXP, or Base precompile changes convert malformed, oversized, or otherwise user-controlled input from normal EVM halt/revert behavior into fatal `Err`, panic, or process exit behavior; changes blur `Error` vs `Halt` semantics after upstream SDK changes. | `crates/common/precompiles`, `crates/common/evm/src/precompiles`, precompile provider/adapters, Reth SDK integration. | `PrecompileError::Fatal`, non-invalid EVM error, panic, validator re-execution failure. | Sequencer can fail to build a block containing the transaction; validators can get stuck re-executing a block that contains it. | Any semantic change to precompile failure mode without explicit builder and validator impact analysis is critical. | Boundary tests at max and ... (truncated)
```

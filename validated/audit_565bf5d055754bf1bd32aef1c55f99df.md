### Title
Gas metering for `yield_resume_byte` is computed from the raw declared length parameter instead of the resolved payload size, allowing fee bypass via register-supplied payloads - (File: runtime/near-vm-runner/src/logic/logic.rs)

### Summary
`promise_yield_resume_with_yield_id` charges the `yield_resume_byte` per-byte fee using the caller-supplied `payload_len` argument at logic.rs:3895, **before** `get_memory_or_register!` resolves the actual payload at logic.rs:3897 and rebinds `payload_len` to the real resolved size at logic.rs:3898. Because NEAR's `get_memory_or_register!` convention treats a sentinel pointer (`payload_ptr == u64::MAX`) as "read from register", the value passed as `payload_len` in that mode is actually interpreted by the macro as a register id, not a byte count, so the fee charged has no relationship to the size of the data actually pulled from the register.

### Finding Description
The function signature and body are: [1](#0-0) 

```
pub fn promise_yield_resume_with_yield_id(...) {
    ...
    self.result_state.gas_counter.pay_base(yield_resume_base)?;
    self.result_state.gas_counter.pay_per(yield_resume_byte, payload_len)?;   // charged on DECLARED len
    let yield_id = get_memory_or_register!(self, yield_id_ptr, yield_id_len)?;
    let payload = get_memory_or_register!(self, payload_ptr, payload_len)?;  // resolves ACTUAL data (may come from register)
    let payload_len = payload.len() as u64;                                 // re-bound to actual size, but no re-charge
    ...
}
```

The `pay_per(yield_resume_byte, payload_len)` call at logic.rs:3895 executes before the payload is resolved by `get_memory_or_register!` at logic.rs:3897. When the guest invokes the host function with `payload_ptr` set to the register sentinel, `payload_len` is no longer a byte count in that branch — it is the register id whose contents are substituted as the real payload. Consequently the fee is computed against an attacker-chosen small integer (the register id) rather than against the actual number of bytes copied out of the register. After resolution, `payload_len` is reassigned to `payload.len()` purely to enforce the `max_yield_payload_size` limit at logic.rs:3899-3905; there is no subsequent `pay_per` call to true up the previously-charged fee to the resolved size.

This is the identical pattern already present in the sibling function `promise_yield_resume` (logic.rs:3855-3859), so the same root cause exists in both the `data_id` and `yield_id` variants.

An attacker's transaction sequence:
1. Deploy a wasm contract that calls `promise_yield_create_with_id` to register a pending yield.
2. Populate a register with a large payload (up to `max_yield_payload_size`) via any host call that writes into registers on the attacker's own contract (e.g. `storage_read`, `promise_result`, or similar).
3. Call `promise_yield_resume_with_yield_id` passing `payload_ptr = u64::MAX` (register sentinel) and `payload_len = <small register id>` (e.g. `0`).
4. The gas counter charges `yield_resume_byte * 0` (or whatever tiny register id value is supplied) instead of `yield_resume_byte * actual_payload_len`, while the actual large payload is processed, validated against `max_yield_payload_size`, and forwarded to `submit_promise_resume_data_with_yield_id`.

No existing check intercepts this: there is no size-limit check before the fee is charged, and the `max_yield_payload_size` check at logic.rs:3899 only bounds correctness/DoS, not economic cost — it does not re-charge gas for the resolved size.

### Impact Explanation
This is a **fee payment bypass**: the attacker can process and store a large yield-resume payload (up to the protocol's `max_yield_payload_size`) while paying gas fees computed against an arbitrarily small declared length, breaking the metering totality invariant for this host function. This lets an attacker extract host-side work/storage (processing and forwarding a large payload through the yield/promise machinery) without paying the fee schedule intended to cover that work, matching the "Fee payment bypass" category rather than fund theft or consensus divergence.

### Likelihood Explanation
Preconditions are entirely attacker-controlled and require no privileged access: any account can deploy a contract, create a yield via `promise_yield_create_with_id`, populate a register with attacker data, and invoke `promise_yield_resume_with_yield_id` with a mismatched register id in place of `payload_len`. The exploit is repeatable on every call and costs only the ordinary base fees for the call itself plus whatever fee was paid to populate the register (which is independent of the `yield_resume_byte` fee being bypassed).

### Recommendation
Move the `pay_per(yield_resume_byte, payload_len)` charge to after `get_memory_or_register!` resolves the actual payload, using the resolved `payload.len()` value, in both `promise_yield_resume` (logic.rs:3855-3859) and `promise_yield_resume_with_yield_id` (logic.rs:3894-3898). This mirrors the correct pattern already used elsewhere in the file where fees are charged against post-resolution register/memory sizes.

### Proof of Concept
Rust integration test plan (in `runtime/near-vm-runner/src/logic/tests/yield_resume.rs`):
1. Deploy a contract that: calls `promise_yield_create_with_id`, writes a payload of size `N` (close to `max_yield_payload_size`) into register `R` via a host call that populates registers, then calls `promise_yield_resume_with_yield_id` with `payload_ptr = u64::MAX`, `payload_len = R` (register id, e.g. `0`).
2. Run a second contract variant that supplies the same `N`-byte payload directly via memory (`payload_ptr` = real pointer, `payload_len = N`).
3. Assert: burnt gas for the register-path call is significantly less than `yield_resume_base + yield_resume_byte * N`, while the memory-path call burns gas consistent with `yield_resume_base + yield_resume_byte * N`.
4. Assert the register-path call still succeeds and the resumed payload is accepted (bounded by `max_yield_payload_size`), demonstrating that a large payload was processed and stored while paying a fee computed on a small declared length — confirming the metering totality violation.

### Citations

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3880-3898)
```rust
    pub fn promise_yield_resume_with_yield_id(
        &mut self,
        yield_id_len: u64,
        yield_id_ptr: u64,
        payload_len: u64,
        payload_ptr: u64,
    ) -> Result<u32, VMLogicError> {
        self.result_state.gas_counter.pay_base(base)?;
        if self.context.is_view() {
            return Err(HostError::ProhibitedInView {
                method_name: "promise_yield_resume_with_yield_id".to_string(),
            }
            .into());
        }
        self.result_state.gas_counter.pay_base(yield_resume_base)?;
        self.result_state.gas_counter.pay_per(yield_resume_byte, payload_len)?;
        let yield_id = get_memory_or_register!(self, yield_id_ptr, yield_id_len)?;
        let payload = get_memory_or_register!(self, payload_ptr, payload_len)?;
        let payload_len = payload.len() as u64;
```

This confirms the premise is unfounded. Key facts:

1. `promise_batch_action_add_key_with_full_access` (in `runtime/near-vm-runner/src/logic/logic.rs:3447-3471`) has **no allowance parameter at all** and never writes anything to any register. It only reads the public key via `get_public_key`. There is no "allowance bytes" it could leave behind in a register for a later call to alias into.

2. Registers are addressed by an explicit `register_id` chosen by the calling contract itself, per `get_memory_or_register` in `runtime/near-vm-runner/src/logic/vmstate.rs:276-288`: if `len == u64::MAX`, `ptr` is treated as the register id and its contents are read via `Registers::get`. This mechanism is entirely under control of the same contract's own wasm code — the attacker's own contract decides which register id to write into and which to read from. There's no host-side "leftover" or unintended aliasing across accounts/callers; each `VMLogic`/`Registers` instance is scoped to a single receipt's single function-call execution by the attacker's own deployed contract, so the attacker already fully knows and controls whatever bytes are sitting in any register it queries.

3. Since the attacker's own contract chooses both the pointer/register arguments for `promise_batch_action_add_key_with_full_access` and `promise_batch_action_add_key_with_function_call`, "aliasing" a register is just the attacker supplying the `method_names_len=u64::MAX, method_names_ptr=register_id` combination on purpose — this is normal, intended usage of the register API, not a boundary violation. The resulting `method_names` are still exactly whatever bytes the attacker's contract put into that register beforehand (e.g. via `write_register`), which the attacker fully controls and intends. There is no cross-account, cross-user, or cross-transaction data bleed; nothing "bleeds" from a "previous, unrelated action" because there is no previous action here that ever wrote allowance data into a register in the first place, and even if some other host call had written something to that register, it would still be something the attacker's own contract code arranged, not injected by a third party. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

#No vulnerability found for this question.

### Citations

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3447-3471)
```rust
    pub fn promise_batch_action_add_key_with_full_access(
        &mut self,
        promise_idx: u64,
        public_key_len: u64,
        public_key_ptr: u64,
        nonce: u64,
    ) -> Result<()> {
        self.result_state.gas_counter.pay_base(base)?;
        if self.context.is_view() {
            return Err(HostError::ProhibitedInView {
                method_name: "promise_batch_action_add_key_with_full_access".to_string(),
            }
            .into());
        }

        let public_key = self.get_public_key(
            public_key_ptr,
            public_key_len,
            self.ext.post_quantum_keys_enabled(),
        )?;
        let (receipt_idx, sir) = self.promise_idx_to_receipt_idx_with_sir(promise_idx)?;
        self.pay_action_base(ActionCosts::add_full_access_key, sir)?;
        self.ext.append_action_add_key_with_full_access(receipt_idx, public_key.decode()?, nonce);
        Ok(())
    }
```

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3492-3538)
```rust
    pub fn promise_batch_action_add_key_with_function_call(
        &mut self,
        promise_idx: u64,
        public_key_len: u64,
        public_key_ptr: u64,
        nonce: u64,
        allowance_ptr: u64,
        receiver_id_len: u64,
        receiver_id_ptr: u64,
        method_names_len: u64,
        method_names_ptr: u64,
    ) -> Result<()> {
        self.result_state.gas_counter.pay_base(base)?;
        if self.context.is_view() {
            return Err(HostError::ProhibitedInView {
                method_name: "promise_batch_action_add_key_with_function_call".to_string(),
            }
            .into());
        }
        let public_key = self.get_public_key(
            public_key_ptr,
            public_key_len,
            self.ext.post_quantum_keys_enabled(),
        )?;
        let allowance = Balance::from_yoctonear(
            self.memory.get_u128(&mut self.result_state.gas_counter, allowance_ptr)?,
        );
        let allowance = if allowance > Balance::ZERO { Some(allowance) } else { None };
        let receiver_id = self.read_and_parse_account_id(receiver_id_ptr, receiver_id_len)?;
        let raw_method_names = get_memory_or_register!(self, method_names_ptr, method_names_len)?;
        let method_names = split_method_names(&raw_method_names)?;

        let (receipt_idx, sir) = self.promise_idx_to_receipt_idx_with_sir(promise_idx)?;

        let num_bytes = null_terminated_method_names_len(&method_names);
        self.pay_action_base(ActionCosts::add_function_call_key_base, sir)?;
        self.pay_action_per_byte(ActionCosts::add_function_call_key_byte, num_bytes, sir)?;

        self.ext.append_action_add_key_with_function_call(
            receipt_idx,
            public_key.decode()?,
            nonce,
            allowance,
            receiver_id,
            method_names,
        )?;
        Ok(())
```

**File:** runtime/near-vm-runner/src/logic/vmstate.rs (L119-150)
```rust
pub(crate) struct Registers {
    /// Values of each existing register.
    registers: std::collections::HashMap<u64, Rc<[u8]>>,

    /// Total memory usage as counted for the purposes of the contract
    /// execution.
    ///
    /// Usage of each register is counted as its value’s length plus eight
    /// (i.e. size of `u64`).  Total usage is sum over all registers.  This only
    /// approximates actual usage in memory.
    total_memory_usage: u64,
}

impl Registers {
    /// Returns register with given index.
    ///
    /// Returns an error if (i) there’s not enough gas to perform the register
    /// read or (ii) register with given index doesn’t exist.
    pub(crate) fn get<'s>(
        &'s self,
        gas_counter: &mut GasCounter,
        register_id: u64,
    ) -> Result<&'s [u8]> {
        if let Some(data) = self.registers.get(&register_id) {
            gas_counter.pay_base(read_register_base)?;
            let len = u64::try_from(data.len()).map_err(|_| HostError::MemoryAccessViolation)?;
            gas_counter.pay_per(read_register_byte, len)?;
            Ok(&data[..])
        } else {
            Err(HostError::InvalidRegisterId { register_id }.into())
        }
    }
```

**File:** runtime/near-vm-runner/src/logic/vmstate.rs (L276-288)
```rust
pub(super) fn get_memory_or_register<'a>(
    gas_counter: &mut GasCounter,
    memory: &'a Memory,
    registers: &'a Registers,
    ptr: u64,
    len: u64,
) -> Result<Cow<'a, [u8]>> {
    if len == u64::MAX {
        registers.get(gas_counter, ptr).map(Cow::Borrowed)
    } else {
        memory.view(gas_counter, MemSlice { ptr, len })
    }
}
```

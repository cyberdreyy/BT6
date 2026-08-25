### Title
Forged `AccountInfo` Pointers Allow CPI Account State Substitution When `syscall_parameter_address_restrictions` Is Inactive - ([File: program-runtime/src/cpi.rs])

### Summary
The `TokenVault` bug is a "trusted identifier → address" mapping that can be silently re-pointed by an unauthenticated update path, letting an attacker substitute a different underlying resource for a known identifier. The analogous condition in Agave's CPI syscall path is that the mapping from a *transaction-verified* account pubkey to its *physical* serialized memory location (lamports/data/owner pointers) is validated only when the `syscall_parameter_address_restrictions` feature (SIMD‑0459) is active. Until that feature is active network‑wide, a program can hand the CPI syscall an `AccountInfo`/`SolAccountInfo` whose `key` matches a legitimate, writable instruction account but whose `lamports`/`data`/`owner` pointers reference attacker-controlled memory instead of the account's real serialized region, exactly mirroring "identifier is validated, but the address behind it is not."

### Finding Description
During CPI, the runtime resolves which caller-supplied `AccountInfo` corresponds to a given trusted instruction account purely by **pubkey value equality**: [1](#0-0) 

The `account_key` here comes from the authoritative `TransactionContext` (analogous to `tokenAddresses[id]` being the trusted registry), but `account_info_keys` are read from **caller-supplied VM memory** whose `key_addr`/`owner_addr`/`lamports_addr`/`data_addr` fields are only pointer-validated when `syscall_parameter_address_restrictions` is enabled: [2](#0-1) [3](#0-2) 

When this feature flag is off, `CallerAccount::from_sol_account_info`/`from_account_info` skip `check_account_info_pointer` entirely and simply translate whatever addresses the caller placed in the `key`, `owner`, `lamports`, and `data` fields — with no requirement that these addresses correspond to the account's actual serialized input region (`account_metadata.vm_key_addr`/`vm_owner_addr`/`vm_lamports_addr`/`vm_data_addr`). The feature is confirmed to still be gated/not-yet-universal, since it is tracked in the `FeatureSnapshot` struct, which is explicitly documented to contain "only features that have not been activated on all clusters": [4](#0-3) [5](#0-4) [6](#0-5) 

This is structurally the same bug class as `TokenVault`: the identifier (`account_key`/currency id) is checked, but the mapping to the underlying storage location (`lamports_addr`/`data_addr`/currency address) is trusted from untrusted input without verifying it matches the registered/expected location. The subsequent sync step, `update_callee_account`, then copies whatever the (possibly forged) `CallerAccount` fields say into the real, trusted `callee_account` obtained from `try_borrow_instruction_account`: [7](#0-6) 

### Impact Explanation
If a malicious or compromised program crafts a forged `AccountInfo`/`SolAccountInfo` for any writable account already present in the current instruction's account list — matching only its `key` bytes — the runtime will treat attacker-controlled scratch memory as that account's authoritative pre-CPI state, and propagate attacker-chosen `lamports`/`data`/`owner` values into the trusted `BorrowedAccount` before invoking the callee. This lets an untrusted program impersonate/substitute the physical backing of a legitimate account reference during CPI, bypassing the implicit assumption that `AccountInfo` fields always alias the runtime's own serialized input buffer — directly analogous to `TokenVault` allowing a currency identifier to be re-pointed to an attacker-chosen address, enabling state/fund confusion for any writable account the malicious program can include in its instruction.

### Likelihood Explanation
Exploitability is entirely gated by the activation status of `syscall_parameter_address_restrictions` (SIMD‑0459) and its companion `virtual_address_space_adjustments`/`account_data_direct_mapping` flags. As long as these remain unactivated on any live cluster, any ordinary user transaction invoking a malicious/compromised BPF program that performs a CPI (`invoke`/`invoke_signed`) can reach this path — no special privilege beyond deploying/calling a program is required, matching the "ordinary user's transaction, deployed program" trigger class.

### Recommendation
Make `check_account_info_pointer` validation of `key`, `owner`, `lamports`, and `data` addresses unconditional in `CallerAccount::from_account_info` / `from_sol_account_info` (program-runtime/src/cpi.rs), rather than gating it behind the `syscall_parameter_address_restrictions` feature, so that every `AccountInfo` field is verified against the account's actual serialized VM location (`SerializedAccountMetadata`) regardless of feature activation state — mirroring the TokenVault fix of ensuring the identifier-to-address association can never be silently altered by untrusted input.

### Proof of Concept
1. Deploy a program `P` that receives, among its instruction accounts, a writable account `V` (e.g., a shared vault/authority account) it is not the owner of, plus a scratch account `S` it controls.
2. Before performing a CPI, `P` builds a raw `SolAccountInfo`/`AccountInfo` array where one entry's `key_addr` points to memory containing `V`'s pubkey bytes, but whose `lamports_addr`/`data_addr`/`owner_addr` point into `S`'s own writable scratch buffer pre-filled with attacker-chosen lamports/data/owner values.
3. `P` calls `invoke()`/`invoke_signed()` referencing this array on a cluster/config where `syscall_parameter_address_restrictions` is inactive (as in `programs/sbf/rust/invoke/src/lib.rs`'s `TEST_CPI_INVALID_KEY_POINTER` test harness, which specifically exercises this pointer-substitution path — see `programs/sbf/c/src/invoke/invoke.c:800-816`).
4. Observe that `update_callee_account` (program-runtime/src/cpi.rs) writes the attacker-chosen lamports/data/owner from `S`'s memory into `V`'s real account state prior to CPI, without the pointer being validated against `V`'s true serialized location.

### Citations

**File:** program-runtime/src/cpi.rs (L108-126)
```rust
/// Check that an account info pointer field points to the expected address
fn check_account_info_pointer(
    invoke_context: &InvokeContext,
    vm_addr: u64,
    expected_vm_addr: u64,
    field: &str,
) -> Result<(), Error> {
    if vm_addr != expected_vm_addr {
        ic_msg!(
            invoke_context,
            "Invalid account info pointer `{}': {:#x} != {:#x}",
            field,
            vm_addr,
            expected_vm_addr
        );
        return Err(Box::new(CpiError::InvalidPointer));
    }
    Ok(())
}
```

**File:** program-runtime/src/cpi.rs (L427-455)
```rust
        if syscall_parameter_address_restrictions {
            check_account_info_pointer(
                invoke_context,
                account_info.key_addr,
                account_metadata.vm_key_addr,
                "key",
            )?;

            check_account_info_pointer(
                invoke_context,
                account_info.owner_addr,
                account_metadata.vm_owner_addr,
                "owner",
            )?;

            check_account_info_pointer(
                invoke_context,
                account_info.lamports_addr,
                account_metadata.vm_lamports_addr,
                "lamports",
            )?;

            check_account_info_pointer(
                invoke_context,
                account_info.data_addr,
                account_metadata.vm_data_addr,
                "data",
            )?;
        }
```

**File:** program-runtime/src/cpi.rs (L1009-1022)
```rust
        let account_key = invoke_context
            .transaction_context
            .get_key_of_account_at_index(instruction_account.index_in_transaction)?;

        #[expect(deprecated)]
        if callee_account.is_executable() {
            // Use the known account
            let amount = (callee_account.get_data().len() as u64)
                .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
                .unwrap_or(u64::MAX);
            invoke_context.compute_meter.consume_checked(amount)?;
        } else if let Some(caller_account_index) =
            account_info_keys.iter().position(|key| *key == account_key)
        {
```

**File:** program-runtime/src/cpi.rs (L1059-1076)
```rust
            let update_caller = if syscall_parameter_address_restrictions {
                // update_callee_account() is moved to cpi_common()
                true
            } else {
                // before initiating CPI, the caller may have modified the
                // account (caller_account). We need to update the corresponding
                // BorrowedAccount (callee_account) so the callee can see the
                // changes.
                update_callee_account(
                    memory_mapping,
                    check_aligned,
                    &caller_account,
                    callee_account,
                    syscall_parameter_address_restrictions,
                    virtual_address_space_adjustments,
                    account_data_direct_mapping,
                )?
            };
```

**File:** feature-set/src/lib.rs (L14-18)
```rust
/// A snapshot of features for faster access without a hash lookup.
/// It should contain only features that have not been activated on
/// all clusters.
/// The order of fields should match the declaration order in
/// [`FEATURE_NAMES`].
```

**File:** feature-set/src/lib.rs (L32-32)
```rust
    pub syscall_parameter_address_restrictions: bool,
```

**File:** feature-set/src/lib.rs (L2100-2103)
```rust
        (
            syscall_parameter_address_restrictions::id(),
            "SIMD-0459: Syscall Parameter Address Restrictions",
        ),
```

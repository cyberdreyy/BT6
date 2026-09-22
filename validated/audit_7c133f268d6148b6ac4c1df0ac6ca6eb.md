### Title
Type Confusion via Unchecked `transmute` of Untrusted Account Data into `LoaderV4State` - (File: svm/src/program_loader.rs)

### Summary
`loader_v4_get_state` in `svm/src/program_loader.rs` transmutes a raw byte slice taken directly from an account's on-chain data into a `&LoaderV4State` reference without validating that the bytes represent a legal value of that type, including its enum field `status: LoaderV4Status`. [1](#0-0) 

### Finding Description
`loader_v4_get_state` only checks that the account data is *long enough* (`data.get(0..LoaderV4State::program_data_offset())`), then performs an `unsafe` `std::mem::transmute` of the raw bytes into a `&LoaderV4State` reference: [2](#0-1) 

`LoaderV4State` contains a `status: LoaderV4Status` field, which is a Rust enum. Enums with a fixed set of discriminants are only valid for the exact discriminant bit-patterns the compiler expects; transmuting arbitrary attacker-supplied bytes into such a type is undefined behavior if the byte pattern does not correspond to one of the enum's valid variants. Unlike the bincode-based paths used for `UpgradeableLoaderState` (loader v3), which go through checked deserialization (`bincode::deserialize`) and reject malformed data gracefully, [3](#0-2)  the loader v4 path bypasses all structural/tag validation and directly reinterprets memory.

This function is called from two unprivileged, transaction-reachable entry points:
- `load_program_accounts`, invoked for any account owned by `loader_v4` that appears in a submitted transaction. [4](#0-3) 
- `get_program_deployment_slot`, used by `filter_executable_program_accounts` while scanning transaction account keys for programs to load into the cache. [5](#0-4) [6](#0-5) 

Because ownership of an account (`account.owner()`) can be set to any program ID (including `loader_v4::id()`) via the System Program's `Assign` instruction — which performs no validation of the resulting account's data format — an unprivileged transaction sender can create an account with a data buffer of at least `LoaderV4State::program_data_offset()` bytes containing an arbitrary bit pattern, assign its owner to `loader_v4`, and reference that account key in a subsequent transaction. This forces the runtime to run the unchecked transmute over attacker-controlled bytes and then pattern-match on the resulting (potentially invalid) `LoaderV4Status` discriminant. [7](#0-6) 

### Impact Explanation
Matching on an enum whose in-memory discriminant does not correspond to any declared variant is undefined behavior in Rust. Depending on codegen (jump tables, niche optimizations, LLVM's `switch` lowering for unreachable defaults), this can manifest as memory-safety violations, or as behavior that differs between validator binaries built with different compiler versions/optimization settings — creating a genuine risk of non-deterministic execution and potential consensus divergence or validator crash triggered purely by a submitted transaction, which is within the accepted impact classes (transaction-triggered cluster halt / consensus divergence). It is markedly more severe than a normal parsing bug because bincode/serde-based paths in the same file fail safely by returning `Err`, whereas this path has no such safety net. [8](#0-7) 

### Likelihood Explanation
The attack requires only: (1) creating/owning an account, (2) issuing a `System::Assign` instruction to set its owner to `loader_v4::id()` (no special privilege required), and (3) referencing that account key in a transaction that is processed by the SVM (either as the invoked program or simply as one of the account keys scanned by `filter_executable_program_accounts`). All three actions are available to any unprivileged transaction sender using standard, publicly documented instructions, requiring no validator or leader cooperation.

### Recommendation
Replace the unchecked `transmute` in `loader_v4_get_state` with a safe, validating parse: verify the `status` byte pattern corresponds to a legal `LoaderV4Status` discriminant (e.g., via `bytemuck::CheckedBitPattern`/`TryFrom<u64>`/manual discriminant validation) before constructing the typed reference, and return `InstructionError::InvalidAccountData` on mismatch, mirroring the safe-parsing pattern already used for `UpgradeableLoaderState`. [2](#0-1) 

### Proof of Concept
1. Attacker submits a transaction: `SystemInstruction::CreateAccount` (or use an existing owned account) to allocate a data buffer of length ≥ `LoaderV4State::program_data_offset()` bytes.
2. Attacker submits `SystemInstruction::Assign` to set the account's owner to `loader_v4::id()`. This instruction performs no validation of the account's existing data content.
3. Attacker fills the account data with a byte pattern where the bytes corresponding to `LoaderV4State::status` do not correspond to any valid `LoaderV4Status` discriminant (e.g., all `0xFF`).
4. Attacker submits any transaction (e.g., a simple transfer) that includes this account's pubkey among its static account keys.
5. During normal transaction processing, `filter_executable_program_accounts` / `load_program_accounts` detects the account is owned by `loader_v4` and calls `loader_v4_get_state(account.data())`, which transmutes the crafted bytes into `&LoaderV4State` and later pattern-matches `state.status`, triggering undefined behavior on an invalid enum tag. [4](#0-3)

### Citations

**File:** svm/src/program_loader.rs (L40-49)
```rust
    let load_result = if loader_v4::check_id(program_account.owner()) {
        loader_v4_get_state(program_account.data())
            .ok()
            .and_then(|state| {
                (!matches!(state.status, LoaderV4Status::Retracted)).then_some(state.slot)
            })
            .map(|slot| ProgramAccountLoadResult::ProgramOfLoaderV4(program_account, slot))
            .unwrap_or(ProgramAccountLoadResult::InvalidAccountData(
                ProgramCacheEntryOwner::LoaderV4,
            ))
```

**File:** svm/src/program_loader.rs (L51-53)
```rust
        if let Ok(UpgradeableLoaderState::Program {
            programdata_address,
        }) = bincode::deserialize(program_account.data())
```

**File:** svm/src/program_loader.rs (L199-230)
```rust
pub(crate) fn get_program_deployment_slot<CB: TransactionProcessingCallback>(
    callbacks: &CB,
    program: &AccountSharedData,
    loader: ProgramCacheEntryOwner,
) -> TransactionResult<Slot> {
    match loader {
        ProgramCacheEntryOwner::LoaderV1 | ProgramCacheEntryOwner::LoaderV2 => Ok(0),
        ProgramCacheEntryOwner::LoaderV3 => {
            if let Ok(UpgradeableLoaderState::Program {
                programdata_address,
            }) = bincode::deserialize(program.data())
            {
                let (programdata, _slot) = callbacks
                    .get_account_shared_data(&programdata_address)
                    .ok_or(TransactionError::ProgramAccountNotFound)?;
                if let Ok(UpgradeableLoaderState::ProgramData {
                    slot,
                    upgrade_authority_address: _,
                }) = bincode::deserialize(programdata.data())
                {
                    return Ok(slot);
                }
            }
            Err(TransactionError::ProgramAccountNotFound)
        }
        ProgramCacheEntryOwner::LoaderV4 => {
            let state = loader_v4_get_state(program.data())
                .map_err(|_| TransactionError::ProgramAccountNotFound)?;
            Ok(state.slot)
        }
        ProgramCacheEntryOwner::NativeLoader => unreachable!(),
    }
```

**File:** svm/src/program_loader.rs (L235-276)
```rust
pub fn filter_executable_program_accounts<'a, CB: TransactionProcessingCallback>(
    callbacks: &CB,
    program_cache_for_tx_batch: &ProgramCacheForTxBatch,
    keys: impl Iterator<Item = &'a Pubkey>,
    check_program_deployment_slot: bool,
) -> Vec<ProgramToLoad<'a>> {
    let mut result = Vec::new();
    for account_key in keys {
        if let Some(cache_entry) = program_cache_for_tx_batch.find(account_key) {
            cache_entry.stats.uses.fetch_add(1, Ordering::Relaxed);
        } else if let Some((account, last_modification_slot)) =
            callbacks.get_account_shared_data(account_key)
        {
            let loader = if loader_v4::check_id(account.owner()) {
                ProgramCacheEntryOwner::LoaderV4
            } else if bpf_loader_upgradeable::check_id(account.owner()) {
                ProgramCacheEntryOwner::LoaderV3
            } else if bpf_loader::check_id(account.owner()) {
                ProgramCacheEntryOwner::LoaderV2
            } else if bpf_loader_deprecated::check_id(account.owner()) {
                ProgramCacheEntryOwner::LoaderV1
            } else {
                continue;
            };
            let match_criteria = if check_program_deployment_slot {
                get_program_deployment_slot(callbacks, &account, loader)
                    .map_or(ProgramCacheMatchCriteria::Tombstone, |slot| {
                        ProgramCacheMatchCriteria::DeployedOnOrAfterSlot(slot)
                    })
            } else {
                ProgramCacheMatchCriteria::NoCriteria
            };
            result.push(ProgramToLoad {
                program_id: account_key,
                loader,
                match_criteria,
                last_modification_slot,
            });
        }
    }
    result
}
```

**File:** svm/src/program_loader.rs (L278-291)
```rust
// Plucked from the now-removed Loader V4 program library.
fn loader_v4_get_state(data: &[u8]) -> Result<&LoaderV4State, InstructionError> {
    unsafe {
        let data = data
            .get(0..LoaderV4State::program_data_offset())
            .ok_or(InstructionError::AccountDataTooSmall)?
            .try_into()
            .unwrap();
        Ok(std::mem::transmute::<
            &[u8; LoaderV4State::program_data_offset()],
            &LoaderV4State,
        >(data))
    }
}
```

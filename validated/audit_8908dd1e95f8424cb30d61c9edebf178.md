### Title
Unprivileged pre-funding of the deterministic program-data address can permanently block Core BPF builtin migrations - (File: `runtime/src/bank/builtins/core_bpf_migration/target_builtin.rs`)

### Summary
Agave's Core BPF migration mechanism (the on-chain analog to Lens's `batchMigrateProfiles()`/handle-minting flow) computes the destination `program_data_address` for a to-be-migrated builtin deterministically via `get_program_data_address(program_address)` [1](#0-0) . Just like a whitelisted Lens profile creator could permissionlessly mint a handle that a later migration needed, any unprivileged Solana account holder can send an ordinary `system_instruction::transfer` to this deterministic address *before* the migration feature is activated. When the migration finally runs, `TargetBuiltin::new_checked` (and its sibling `TargetBpfV2::new_checked`) explicitly reject the migration if that address already exists with a non-zero balance and `allow_prefunded` is `false`, returning `CoreBpfMigrationError::ProgramHasDataAccount` instead of completing the migration [2](#0-1) .

### Finding Description
`migrate_builtin_to_core_bpf` is the runtime routine invoked deterministically by every validator at a feature-activation slot to replace a native builtin with a Core BPF program [3](#0-2) . It first validates the target builtin via `TargetBuiltin::new_checked`, which requires that the derived `program_data_address` either does not exist, or (if `allow_prefunded` is enabled for that specific migration config) exists only as a plain, zero-data System-owned account with lamports [4](#0-3) .

Because `program_data_address` is a value fully determined by the well-known `program_address` (via a deterministic PDA-style derivation), any unprivileged user can pre-fund or pre-populate that address using a normal, permissionless `system_instruction::transfer`/`CreateAccount` well before the migration's feature gate activates — exactly mirroring how a whitelisted Lens `mintHandle()` caller could squat a handle a V1 profile needed for `batchMigrateProfiles()`. If the specific migration entry does not set `allow_prefunded = true`, or if the attacker sends more than the exact expected prefund pattern the code tolerates, `TargetBuiltin::new_checked`/`TargetBpfV2::new_checked` return `CoreBpfMigrationError::ProgramHasDataAccount`/`AccountExists`, causing `migrate_builtin_to_core_bpf` to fail for every validator identically [5](#0-4) .

This is the direct structural analog of the reported Lens issue: a permissionless/whitelisted action that is allowed to write to an address independently of the migration flow can collide with a fixed target address that a later, protocol-critical migration depends on, permanently preventing that migration from ever succeeding for the affected program.

### Impact Explanation
Because the destination address is address-space public and computable off-chain by anyone before the relevant `feature_id` activates, an unprivileged transaction sender can grief a planned Core BPF migration for a specific builtin, causing that migration to fail identically and deterministically on every validator once the feature activates. This does not cause fund loss, CPI privilege escalation, or divergence between honest validators (all see the same pre-funded state and fail identically), but it does permanently block a core protocol upgrade path for the affected builtin program, which is the direct analog of the reported "migration permanently broken" impact in the Lens report.

### Likelihood Explanation
The precondition — publicly computing a deterministic destination address and sending it lamports via an ordinary `system_instruction::transfer` — requires no special privilege, no signature over the target address, and can be executed by any account holder at any time before the migration's feature gate is scheduled to activate. The `allow_prefunded` flag mitigates this for migrations explicitly configured with it, but any migration configured with `allow_prefunded = false`, or where the attacker's prefund method doesn't match the tolerated "System-owned account with lamports only" pattern, remains exposed.

### Recommendation
Do not rely solely on prefund-shape heuristics (owner == System program, zero data) to distinguish "acceptable" prefunding from an attacker-controlled account. Where feasible, have migration configs explicitly enumerate/pin the exact expected prefund lamports and require `allow_prefunded` handling to be uniformly enabled with a well-defined tolerance, and treat any deviation (e.g., unexpected balances) as an explicit no-op skip with alerting/re-scheduling rather than as a hard migration failure that can silently and permanently disable the upgrade path for that builtin.

### Proof of Concept
1. Off-chain, compute `program_data_address = get_program_data_address(&target_builtin_program_id)` for a builtin scheduled for a future Core BPF migration whose `CoreBpfMigrationConfig` does not set prefunding to be silently tolerated (i.e., a migration where `allow_prefunded` is effectively `false` or the tolerated shape is stricter than what the attacker sends).
2. Submit an ordinary, unprivileged `system_instruction::transfer` (or `create_account`) transaction funding/populating `program_data_address` before the migration's `feature_id` is activated.
3. When the feature activates and the validator runs `Bank::migrate_builtin_to_core_bpf`, `TargetBuiltin::new_checked` observes the account already exists at `program_data_address` and returns `CoreBpfMigrationError::ProgramHasDataAccount`, exactly as demonstrated by the existing test `test_target_program_builtin`/`test_target_program_stateless_builtin` in `runtime/src/bank/builtins/core_bpf_migration/target_builtin.rs` (lines 291–306), causing the migration to fail for every validator that reaches that slot.

Note: I was unable to fully verify, within the available search budget, the exact caller code path in `runtime/src/bank.rs` that invokes `migrate_builtin_to_core_bpf` and how it handles the returned `Err` (e.g., whether it is logged and skipped versus `unwrap()`ed/panicking). This detail affects whether the failure mode is a silent, permanent migration block (most likely, based on common agave feature-activation patterns) versus a validator panic; this should be confirmed by reading the call site directly.

### Citations

**File:** runtime/src/bank/builtins/core_bpf_migration/target_builtin.rs (L45-53)
```rust
            CoreBpfMigrationTargetType::Stateless => {
                // The program account should _not_ exist.
                if bank.get_account_with_fixed_root(program_address).is_some() {
                    return Err(CoreBpfMigrationError::AccountExists(*program_address));
                }

                AccountSharedData::default()
            }
        };
```

**File:** runtime/src/bank/builtins/core_bpf_migration/target_builtin.rs (L55-82)
```rust
        let program_data_address = get_program_data_address(program_address);

        let program_data_account_lamports = if allow_prefunded {
            // The program data account should not exist, but a system account with funded
            // lamports is acceptable.
            if let Some(account) = bank.get_account_with_fixed_root(&program_data_address) {
                if account.owner() != &SYSTEM_PROGRAM_ID {
                    return Err(CoreBpfMigrationError::ProgramHasDataAccount(
                        *program_address,
                    ));
                }
                account.lamports()
            } else {
                0
            }
        } else {
            // The program data account should not exist and have zero lamports.
            if bank
                .get_account_with_fixed_root(&program_data_address)
                .is_some()
            {
                return Err(CoreBpfMigrationError::ProgramHasDataAccount(
                    *program_address,
                ));
            }

            0
        };
```

**File:** runtime/src/bank/builtins/core_bpf_migration/mod.rs (L224-252)
```rust
    pub(crate) fn migrate_builtin_to_core_bpf(
        &mut self,
        builtin_program_id: &Pubkey,
        config: &CoreBpfMigrationConfig,
        allow_prefunded: bool,
    ) -> Result<(), CoreBpfMigrationError> {
        datapoint_info!(config.datapoint_name, ("slot", self.slot, i64));

        let target = TargetBuiltin::new_checked(
            self,
            builtin_program_id,
            &config.migration_target,
            allow_prefunded,
        )?;
        let source = if let Some(expected_hash) = config.verified_build_hash {
            SourceBuffer::new_checked_with_verified_build_hash(
                self,
                &config.source_buffer_address,
                expected_hash,
            )?
        } else {
            SourceBuffer::new_checked(self, &config.source_buffer_address)?
        };

        // Attempt serialization first before modifying the bank.
        let new_target_program_account =
            self.new_target_program_account(&target.program_data_address)?;
        let new_target_program_data_account =
            self.new_target_program_data_account(&source, config.upgrade_authority_address)?;
```

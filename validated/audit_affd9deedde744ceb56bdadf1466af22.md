### Title
Permissionless `ExtendProgram` instruction can grief legitimate program upgrades/extensions via same-slot `slot` field collision - (File: `programs/bpf_loader/src/lib.rs`)

### Summary
The `bpf_loader_upgradeable` program's `ExtendProgram` instruction can be invoked by *any* unprivileged account (no upgrade-authority signature required), and its handler rejects the operation with `InstructionError::InvalidArgument` if the `ProgramData` account's stored `slot` field already equals the current `Clock::slot`. Because `ExtendProgram` itself writes `clock_slot` into that same field on success, an attacker can front-run a victim's legitimate `Upgrade`/`ExtendProgram`/`ExtendProgramChecked` transaction in the same slot by submitting a cheap, unauthorized `ExtendProgram` call first, causing the victim's transaction to fail — the same "griefable, same-block/slot state field" pattern described in the Uniswap `validatePrice()` report, where a permissionless `sync()`-like call sets a timestamp/slot field that a subsequent validation check compares against.

### Finding Description
`common_extend_program()` is invoked for the plain `UpgradeableLoaderInstruction::ExtendProgram` variant with `check_authority = false`: [1](#0-0) 

Inside `common_extend_program`, the authority-signature check is only performed `if check_authority`, meaning the plain (non-`Checked`) `ExtendProgram` path skips authority validation entirely — anyone can call it as long as they pay the rent-exemption top-up: [2](#0-1) 

Crucially, right before the (skippable) authority check, the function unconditionally rejects the call if the `ProgramData`'s stored `slot` already equals the current `Clock::slot`:
```
if clock_slot == slot {
    ic_logger_msg!(log_collector, "Program was extended in this block already");
    return Err(InstructionError::InvalidArgument);
}
``` [3](#0-2) 

On success, the same field is overwritten with the current slot: [4](#0-3) 

This mirrors the reported Uniswap pattern exactly: a permissionless, state-mutating call (`sync()` / here, an authority-less `ExtendProgram`) updates a "last-updated" field (`blockTimestampLast` / here, `ProgramData.slot`) to the current time unit (block timestamp / slot). A subsequent legitimate operation's validation check (`validatePrice()`'s `block.timestamp == blockTimestampLast` / here, `clock_slot == slot`) then fails solely because the field was already touched in the same time unit — regardless of who touched it. Since a real upgrade authority's `Upgrade` instruction shares this same `ProgramData.slot` field convention (as referenced by the CLI/tests comment "Program was deployed in this block already"), a griefer can also block a legitimate `Upgrade` in the same manner by calling `ExtendProgram` first in that slot.

### Impact Explanation
An attacker who knows (from the public mempool/gossip of pending transactions, or simply by monitoring known upgradeable program addresses) that a program owner intends to submit `Upgrade` or `ExtendProgram`/`ExtendProgramChecked` in the current slot can front-run with a single cheap, unauthorized `ExtendProgram { additional_bytes: 1 }` call (paying only the marginal rent-exemption cost, which is refundable/reclaimable by the program owner later but still a griefing cost paid by the attacker each time). This causes the victim's transaction to fail with `InstructionError::InvalidArgument` for that slot, forcing a retry in a later slot — repeatable every slot the victim tries via the public path. This is a denial-of-service/griefing vector against program deployment/maintenance operations (upgrades, program-size extensions), not a fund-theft or consensus-divergence bug, so impact is limited to availability/reliability of program upgrade operations rather than direct loss of funds.

### Likelihood Explanation
Likelihood is moderate: it requires the attacker to observe or predict a specific program's pending `Upgrade`/`ExtendProgram` transaction before it lands (feasible via mempool/RPC monitoring of a known program's upgrade authority activity or scheduled deployment). Execution cost is low (one cheap permissionless instruction per slot), and the check is unconditional and applies to all upgradeable BPF Loader v3 programs.

### Recommendation
Do not fail `ExtendProgram`/`Upgrade` purely because the `ProgramData.slot` already equals the current slot when the party writing it was not the account's own authorized caller in this same instruction. At minimum, tie the "already modified this slot" rejection to whether *this* transaction/authority actually intends to reuse the collision, or track same-slot modification via a monotonic in-batch/program-cache marker keyed off the authority's own prior action rather than a globally-writable, permissionless-updatable slot field. Alternatively, require the true upgrade authority's signature (or another non-griefable freshness signal) before allowing any account to advance `ProgramData.slot`, so the field cannot be poisoned by unrelated third parties.

### Proof of Concept
1. Program `P` with `ProgramData` account `PD` has an upgrade authority `A`. `A` builds and submits an `Upgrade` (or `ExtendProgramChecked`) transaction for slot `S`.
2. Attacker `M` (no relation to `P`, no signature needed) observes the pending transaction and submits `ExtendProgram(programdata=PD, program=P, additional_bytes=1, payer=M)` with higher priority so it lands first in slot `S`. This succeeds because `common_extend_program` with `check_authority=false` never checks any authority signer, per [5](#0-4) , and sets `PD.slot = S`.
3. `A`'s `Upgrade`/`ExtendProgram(Checked)` transaction subsequently executes in slot `S`, hits `if clock_slot == slot { return Err(InstructionError::InvalidArgument) }` at [6](#0-5) , and fails — confirmed by the existing repo test that shows this same-slot rejection behavior (`test_failed_extend_twice_in_same_slot`) which the attacker can trigger against a victim who did not perform the first call: [7](#0-6) 
4. `M` repeats step 2 in every subsequent slot `A` retries, indefinitely blocking `A`'s program maintenance operations through the public mempool.

### Citations

**File:** programs/bpf_loader/src/lib.rs (L790-792)
```rust
        UpgradeableLoaderInstruction::ExtendProgram { additional_bytes } => {
            common_extend_program(invoke_context, additional_bytes, false)?;
        }
```

**File:** programs/bpf_loader/src/lib.rs (L898-933)
```rust
    let clock_slot = invoke_context
        .environment_config
        .sysvar_cache()
        .get_clock()
        .map(|clock| clock.slot)?;

    let upgrade_authority_address = if let UpgradeableLoaderState::ProgramData {
        slot,
        upgrade_authority_address,
    } = programdata_account.get_state()?
    {
        if clock_slot == slot {
            ic_logger_msg!(log_collector, "Program was extended in this block already");
            return Err(InstructionError::InvalidArgument);
        }

        if upgrade_authority_address.is_none() {
            ic_logger_msg!(
                log_collector,
                "Cannot extend ProgramData accounts that are not upgradeable"
            );
            return Err(InstructionError::Immutable);
        }

        if check_authority {
            let authority_key =
                Some(*instruction_context.get_key_of_instruction_account(AUTHORITY_ACCOUNT_INDEX)?);
            if upgrade_authority_address != authority_key {
                ic_logger_msg!(log_collector, "Incorrect upgrade authority provided");
                return Err(InstructionError::IncorrectAuthority);
            }
            if !instruction_context.is_instruction_account_signer(AUTHORITY_ACCOUNT_INDEX)? {
                ic_logger_msg!(log_collector, "Upgrade authority did not sign");
                return Err(InstructionError::MissingRequiredSignature);
            }
        }
```

**File:** programs/bpf_loader/src/lib.rs (L987-992)
```rust
    let mut programdata_account =
        instruction_context.try_borrow_instruction_account(PROGRAM_DATA_ACCOUNT_INDEX)?;
    programdata_account.set_state(&UpgradeableLoaderState::ProgramData {
        slot: clock_slot,
        upgrade_authority_address,
    })?;
```

**File:** programs/bpf-loader-tests/tests/extend_program_ix.rs (L91-177)
```rust
#[tokio::test]
async fn test_failed_extend_twice_in_same_slot() {
    let mut context = setup_test_context(LoaderV3Features {
        minimum_extend_program_size: false,
    })
    .await;
    let program_file = find_file("noop.so").expect("Failed to find the file");
    let data = read_file(program_file);
    let upgrade_authority = Keypair::new();

    let program_address = Pubkey::new_unique();
    let (programdata_address, _) = Pubkey::find_program_address(&[program_address.as_ref()], &id());
    add_upgradeable_loader_account(
        &mut context,
        &program_address,
        &UpgradeableLoaderState::Program {
            programdata_address,
        },
        UpgradeableLoaderState::size_of_program(),
        |_| {},
    )
    .await;
    let programdata_data_offset = UpgradeableLoaderState::size_of_programdata_metadata();
    let program_data_len = data.len() + programdata_data_offset;
    add_upgradeable_loader_account(
        &mut context,
        &programdata_address,
        &UpgradeableLoaderState::ProgramData {
            slot: 0,
            upgrade_authority_address: Some(upgrade_authority.pubkey()),
        },
        program_data_len,
        |account| account.data_as_mut_slice()[programdata_data_offset..].copy_from_slice(&data),
    )
    .await;

    let client = &mut context.banks_client;
    let payer = &context.payer;
    let recent_blockhash = context.last_blockhash;
    const ADDITIONAL_BYTES: u32 = 42;
    let transaction = Transaction::new_signed_with_payer(
        &[extend_program(
            &program_address,
            Some(&payer.pubkey()),
            ADDITIONAL_BYTES,
        )],
        Some(&payer.pubkey()),
        &[payer],
        recent_blockhash,
    );

    assert_matches!(client.process_transaction(transaction).await, Ok(()));
    let updated_program_data_account = client
        .get_account(programdata_address)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        updated_program_data_account.data().len(),
        program_data_len + ADDITIONAL_BYTES as usize
    );

    let recent_blockhash = client
        .get_new_latest_blockhash(&recent_blockhash)
        .await
        .unwrap();
    // Extending the program in the same slot should fail
    let transaction = Transaction::new_signed_with_payer(
        &[extend_program(
            &program_address,
            Some(&payer.pubkey()),
            ADDITIONAL_BYTES,
        )],
        Some(&payer.pubkey()),
        &[payer],
        recent_blockhash,
    );

    assert_matches!(
        client
            .process_transaction(transaction)
            .await
            .unwrap_err()
            .unwrap(),
        TransactionError::InstructionError(0, InstructionError::InvalidArgument)
    );
}
```

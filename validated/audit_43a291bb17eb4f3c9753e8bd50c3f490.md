Root cause found: `InitializeBuffer` in the BPF Upgradeable Loader has no signer check at all, unlike Balancer's `init()` which at least reverts on double-init but has no access control. This Agave instruction lacks *any* access control on who may set the authority.

### Title
Unauthenticated `InitializeBuffer` in BPF Upgradeable Loader allows front-running of buffer authority, enabling theft of rent lamports - (File: programs/bpf_loader/src/lib.rs)

### Summary
The `UpgradeableLoaderInstruction::InitializeBuffer` handler in the BPF Upgradeable Loader only checks that the target buffer account is in the `Uninitialized` state; it performs no signature check on the buffer account itself and no signature check on the account whose key is adopted as `authority_address`.

### Finding Description
```
UpgradeableLoaderInstruction::InitializeBuffer => {
    instruction_context.check_number_of_instruction_accounts(2)?;
    let mut buffer = instruction_context.try_borrow_instruction_account(0)?;
    if UpgradeableLoaderState::Uninitialized != buffer.get_state()? {
        return Err(InstructionError::AccountAlreadyInitialized);
    }
    let authority_key = Some(*instruction_context.get_key_of_instruction_account(1)?);
    buffer.set_state(&UpgradeableLoaderState::Buffer { authority_address: authority_key })?;
}
``` [1](#0-0) 

Neither account index 0 (`buffer`) nor account index 1 (`authority`) is required to be a signer — this is confirmed by the existing unit test, which builds the `InitializeBuffer` instruction with both accounts marked `is_signer: false` and still expects `Ok(())`: [2](#0-1) 

This is the direct analog of the reported `BalancerLBPSwapper.init()` bug: an account is created in one step (e.g. `CreateAccount` allocating and funding a buffer account owned by `bpf_loader_upgradeable`), and a separate, later instruction (`InitializeBuffer`) sets the privileged "authority" for that account — but the later instruction has no protection ensuring only the legitimate creator/deployer can call it. Compare this to every other state-mutating instruction in the same file (`Write`, `SetAuthority`, `DeployWithMaxDataLen`, `Close`, etc.), all of which explicitly verify `is_instruction_account_signer` for the authority before mutating state, e.g.: [3](#0-2) 

Once an attacker's `InitializeBuffer` transaction lands first (setting themselves as `authority_address`), the legitimate deployer's later `InitializeBuffer` call will fail with `AccountAlreadyInitialized`, forcing a re-deploy — exactly the griefing scenario described in the external report. Worse, because the attacker is now the recorded authority, they can subsequently call `UpgradeableLoaderInstruction::Close` (which does correctly check the authority's signature against the stored `authority_address`) to redirect the buffer account's rent-exempt lamports — funded by the legitimate creator's `CreateAccount` call — to a recipient of the attacker's choosing: [4](#0-3) 

### Impact Explanation
If a buffer account's `CreateAccount` and `InitializeBuffer` steps are not bundled atomically in the same transaction (e.g., in scripted or manual multi-transaction deployment flows), a third party can race to call `InitializeBuffer` first with no signature requirement, hijacking the buffer's authority. This lets the attacker drain the account's rent-exempt lamports via `Close`, constituting unsigned fund movement, and/or grief the legitimate deployer by forcing them to abandon the account.

### Likelihood Explanation
Exploitability depends entirely on whether `CreateAccount` and `InitializeBuffer` are submitted as two separate transactions in practice. I was unable to fully confirm, within available tool budget, whether the standard `loader_v3_instruction::create_buffer` builder (referenced from `cli/src/program.rs`) always bundles both instructions into a single atomic message in every code path that constructs a buffer account — I could not locate/resolve that builder's crate in the index to verify this with certainty. If in all standard tooling paths the two instructions are always packed into one transaction, the practical window for front-running is eliminated for the default CLI flow; the vulnerability would then only be reachable by custom integrators who split these steps across transactions (mirroring exactly the caveat in the original report about `v2Phase1.js`-style multi-transaction deployment). Regardless of this caveat, the missing signer check in `InitializeBuffer` itself is a genuine defect: it is inconsistent with every sibling instruction in the same module and provides no defense-in-depth if any caller (CLI, SDK, or custom program) ever separates account creation from buffer initialization.

### Recommendation
Require `is_instruction_account_signer` on the buffer account (account index 0) in `InitializeBuffer`, mirroring the signer requirement already used for `Write`/`SetAuthority`/`Close`. Since the buffer account address is normally a freshly generated keypair controlled by the deployer, requiring its own signature on `InitializeBuffer` (just as `CreateAccount` already requires) closes the front-running window entirely, consistent with how Solana account creation already prevents squatting via keypair signatures — this instruction should not be the outlier that omits it.

### Proof of Concept
1. Deployer submits transaction A: `system_instruction::create_account` funding `buffer_address` (rent-exempt lamports) with owner `bpf_loader_upgradeable`.
2. Before transaction A's follow-up `InitializeBuffer` lands, attacker observes the pending uninitialized buffer account and submits their own transaction B: `InitializeBuffer` on `buffer_address` naming `attacker_pubkey` as the authority account (no signature required for either account per `programs/bpf_loader/src/lib.rs:158-172`).
3. Deployer's original `InitializeBuffer` now fails with `AccountAlreadyInitialized`.
4. Attacker submits `UpgradeableLoaderInstruction::Close` signed as `attacker_pubkey` (matching the stored `authority_address`), draining the buffer account's rent-exempt lamports to an account of their choosing per `programs/bpf_loader/src/lib.rs:686-716`.

### Citations

**File:** programs/bpf_loader/src/lib.rs (L158-172)
```rust
        UpgradeableLoaderInstruction::InitializeBuffer => {
            instruction_context.check_number_of_instruction_accounts(2)?;
            let mut buffer = instruction_context.try_borrow_instruction_account(0)?;

            if UpgradeableLoaderState::Uninitialized != buffer.get_state()? {
                ic_logger_msg!(log_collector, "Buffer account already initialized");
                return Err(InstructionError::AccountAlreadyInitialized);
            }

            let authority_key = Some(*instruction_context.get_key_of_instruction_account(1)?);

            buffer.set_state(&UpgradeableLoaderState::Buffer {
                authority_address: authority_key,
            })?;
        }
```

**File:** programs/bpf_loader/src/lib.rs (L177-190)
```rust
            if let UpgradeableLoaderState::Buffer { authority_address } = buffer.get_state()? {
                if authority_address.is_none() {
                    ic_logger_msg!(log_collector, "Buffer is immutable");
                    return Err(InstructionError::Immutable); // TODO better error code
                }
                let authority_key = Some(*instruction_context.get_key_of_instruction_account(1)?);
                if authority_address != authority_key {
                    ic_logger_msg!(log_collector, "Incorrect buffer authority provided");
                    return Err(InstructionError::IncorrectAuthority);
                }
                if !instruction_context.is_instruction_account_signer(1)? {
                    ic_logger_msg!(log_collector, "Buffer authority did not sign");
                    return Err(InstructionError::MissingRequiredSignature);
                }
```

**File:** programs/bpf_loader/src/lib.rs (L686-716)
```rust
        UpgradeableLoaderInstruction::Close => {
            instruction_context.check_number_of_instruction_accounts(2)?;
            if instruction_context.get_index_of_instruction_account_in_transaction(0)?
                == instruction_context.get_index_of_instruction_account_in_transaction(1)?
            {
                ic_logger_msg!(
                    log_collector,
                    "Recipient is the same as the account being closed"
                );
                return Err(InstructionError::InvalidArgument);
            }
            let mut close_account = instruction_context.try_borrow_instruction_account(0)?;
            let close_key = *close_account.get_key();
            let close_account_state = close_account.get_state()?;
            close_account.set_data_length(UpgradeableLoaderState::size_of_uninitialized())?;
            match close_account_state {
                UpgradeableLoaderState::Uninitialized => {
                    let mut recipient_account =
                        instruction_context.try_borrow_instruction_account(1)?;
                    recipient_account.checked_add_lamports(close_account.get_lamports())?;
                    close_account.set_lamports(0)?;

                    ic_logger_msg!(log_collector, "Closed Uninitialized {}", close_key);
                }
                UpgradeableLoaderState::Buffer { authority_address } => {
                    instruction_context.check_number_of_instruction_accounts(3)?;
                    drop(close_account);
                    common_close_account(&authority_address, &instruction_context, &log_collector)?;

                    ic_logger_msg!(log_collector, "Closed Buffer {}", close_key);
                }
```

**File:** programs/bpf_loader/src/lib.rs (L1408-1441)
```rust
    fn test_bpf_loader_upgradeable_initialize_buffer() {
        let loader_id = bpf_loader_upgradeable::id();
        let buffer_address = Pubkey::new_unique();
        let buffer_account =
            AccountSharedData::new(1, UpgradeableLoaderState::size_of_buffer(9), &loader_id);
        let authority_address = Pubkey::new_unique();
        let authority_account =
            AccountSharedData::new(1, UpgradeableLoaderState::size_of_buffer(9), &loader_id);
        let instruction_data =
            bincode::serialize(&UpgradeableLoaderInstruction::InitializeBuffer).unwrap();
        let instruction_accounts = vec![
            AccountMeta {
                pubkey: buffer_address,
                is_signer: false,
                is_writable: true,
            },
            AccountMeta {
                pubkey: authority_address,
                is_signer: false,
                is_writable: false,
            },
        ];

        // Case: Success
        let accounts = process_instruction(
            &loader_id,
            &instruction_data,
            vec![
                (buffer_address, buffer_account),
                (authority_address, authority_account),
            ],
            instruction_accounts.clone(),
            Ok(()),
        );
```

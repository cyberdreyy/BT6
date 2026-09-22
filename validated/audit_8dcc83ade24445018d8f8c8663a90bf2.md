## Analysis

The Sherlock finding describes a classic **unauthenticated `initialize()` front-run**: the target contract lets anyone call `initialize()` on an uninitialized account and unilaterally set themselves as the privileged "admin," because the function checks only that the account hasn't already been initialized — not that the caller has any right to be the new admin.

The closest reachable analog in `agave` is the BPF Upgradeable Loader's `InitializeBuffer` instruction handler.

### Title
Unauthenticated `InitializeBuffer` allows anyone to front-run and hijack a program buffer's upgrade authority - ([File: programs/bpf_loader/src/lib.rs])

### Summary
`UpgradeableLoaderInstruction::InitializeBuffer` sets the `authority_address` of a program buffer account from whatever pubkey is passed as instruction account index 1, with **no requirement that this account sign the transaction** and no check that the transaction sender has any relationship to the buffer account's true owner. The only guard is that the buffer must currently be `UpgradeableLoaderState::Uninitialized`.

### Finding Description
In the loader-upgradeable instruction processor: [1](#0-0) 

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

There is no `is_instruction_account_signer` check on account index 1 (the prospective authority), and no check that the transaction sender is related to the account that funded/created the buffer. This is the direct analog of the Sherlock `HardenedTopupProxy.initialize()` bug: whoever's transaction lands first on an uninitialized target account becomes the privileged authority.

Contrast this with every other authority-mutating instruction in the same file — `Write`, `SetAuthority`, `SetAuthorityChecked`, `DeployWithMaxDataLen` — all of which explicitly verify `is_instruction_account_signer` for the authority account, e.g.: [2](#0-1) 

```rust
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

`InitializeBuffer` alone lacks this signer check, because at the time it runs there is *no prior authority to check against* — it is the initialization step. That is exactly the bug pattern from the report: nothing stops an unrelated party from being the first to call it.

### Impact Explanation
If a buffer account is created (via `SystemInstruction::CreateAccount`, assigned to `bpf_loader_upgradeable`) in one transaction, and `InitializeBuffer` is submitted as a separate, later transaction (rather than atomically bundled as the SDK helper `bpf_loader_upgradeable::create_buffer` does), an attacker who observes the pending/confirmed buffer-account creation can race to submit `InitializeBuffer` first, naming themselves (or any pubkey they control) as `authority_address`. This grants the attacker exclusive control (`Write`, `SetAuthority`, `Close`) over that buffer account going forward, since all subsequent instructions check authority against whatever was set here. A victim who is unaware of the race would see their own legitimate `InitializeBuffer` fail with `AccountAlreadyInitialized`, but if they don't notice and proceed to fund the account further, the attacker retains custody of, and can drain lamports from, or supply malicious bytecode to, the buffer via `Close`/`Write`. This maps to CPI/authority privilege escalation over the program-deployment control plane.

### Likelihood Explanation
Requires the buffer account to already exist on-chain as an uninitialized `bpf_loader_upgradeable`-owned account when the attacker submits their `InitializeBuffer` transaction — i.e., the create-then-initialize sequence must not be executed atomically within a single transaction. The standard SDK helper (`bpf_loader_upgradeable::create_buffer`) bundles `CreateAccount` + `InitializeBuffer` into the same transaction, closing this window in the common case. Likelihood is therefore contingent on callers splitting account creation and initialization across transactions rather than using the standard atomic helper — a real but not universal usage pattern.

### Recommendation
Require the account at instruction index 1 to be a transaction signer in `InitializeBuffer`, consistent with the signer checks already enforced in `Write`, `SetAuthority`, and `SetAuthorityChecked`. This closes the window for unauthenticated frontrunning of the authority assignment.

### Proof of Concept
1. User A submits `SystemInstruction::CreateAccount` creating buffer account `B`, assigned to `bpf_loader_upgradeable::id()`, in transaction T1.
2. Once T1 lands, `B` exists on-chain in state `UpgradeableLoaderState::Uninitialized`.
3. Attacker observes `B`'s state and submits a transaction containing `UpgradeableLoaderInstruction::InitializeBuffer` with account 0 = `B` and account 1 = attacker's own pubkey (non-signer).
4. Per `process_loader_upgradeable_instruction` ( [1](#0-0) ), the instruction succeeds without requiring attacker's account to sign, setting `B`'s `authority_address` to the attacker's pubkey.
5. User A's subsequent `InitializeBuffer` for `B` now fails with `AccountAlreadyInitialized`; attacker now controls `B`'s authority and can `Write`/`Close`/`SetAuthority` on it.

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

**File:** programs/bpf_loader/src/lib.rs (L182-190)
```rust
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

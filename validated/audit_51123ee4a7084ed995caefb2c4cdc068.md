### Title
Unenforced assumption that `AccountConfiguration` is deployed at its hardcoded EIP-8130 address allows enshrined authorization state to be silently reaped by EIP-161 clearing - (File: `crates/common/evm/src/zenith.rs`)

### Summary
The Zenith/EIP-8130 system-account safety net force-deploys a non-empty code stub only onto `NonceManagerStorage::ADDRESS`, explicitly excluding `AccountConfiguration`'s hardcoded address from protection because "`AccountConfiguration` is a genuinely deployed contract... on every chain where EIP-8130 is enabled." That assumption is documented in a comment only and is never checked or enforced by any protocol code path, unlike the analogous `Canyon` create2-deployer transition and the `NonceManager` stub transition, both of which actively force-deploy code rather than merely assuming it exists.

### Finding Description
`Eip8130Contracts::ACCOUNT_CONFIG` is a hardcoded CREATE2-derived address [1](#0-0) , and `AccountConfigurationStorage::ADDRESS` is pinned to it [2](#0-1) . The enshrined EIP-8130 execution path (`ActorAuthorizer`, `AccountChangeApplier`, mempool validator, etc.) reads and writes storage directly at this address via the journal/state, completely bypassing the EVM and any code-existence check [3](#0-2) .

The Zenith upgrade transition, which is the only piece of protocol code that defends EIP-8130 system accounts against being reaped by EIP-161 end-of-block state clearing, deliberately excludes `AccountConfiguration` from the set of accounts it protects with a force-planted code stub, reasoning only from a comment that the contract is "genuinely deployed" on every chain where the fork is active: [4](#0-3) 

Unlike `Canyon::ensure_create2_deployer`, which unconditionally force-deploys the create2-deployer bytecode at its hardcoded address on fork activation [5](#0-4) , and unlike the `Zenith` transition's own handling of `NonceManagerStorage`, there is no equivalent mechanism that force-deploys `AccountConfiguration`'s bytecode, nor any runtime assertion that code actually exists at `Eip8130Contracts::ACCOUNT_CONFIG` before the enshrined path starts mutating its storage. If any chain enables the Zenith/EIP-8130 upgrade without the exact deployment step having occurred at that pinned address (misconfigured devnet/testnet, chain-config mismatch, or a deployment step omitted from the upgrade's irregular-state-transition or genesis process), the account at `ACCOUNT_CONFIG` has empty code and empty balance/nonce. Per EIP-161, "emptiness" is defined purely by `balance == 0 && nonce == 0 && code == empty` (storage content is irrelevant to the emptiness test), so this account remains EIP-161-empty even after the enshrined applier writes actor/authorization storage into it.

### Impact Explanation
Any unprivileged EIP-8130 transaction sender can drive `AccountChangeApplier`/`ActorAuthorizer` to write actor authorization, revocation, and account-creation state into `AccountConfigurationStorage`'s slots at the assumed-deployed address. If that address is in fact empty of code (the unenforced assumption fails), the account stays classified as "empty" and is subject to being wiped by end-of-block state clearing, silently discarding all authorization/actor state written by users in that block. This causes permanent loss of account-abstraction authorization state (a freezing/loss-of-funds-adjacent bug, since payers/actors lose their configured signing rights without any error surfaced to the user) and creates state-consistency risk between nodes that differ in the timing/order of applying the enshrined writes versus EIP-161 clearing within the same block.

### Likelihood Explanation
This is directly reachable by a single ordinary EIP-8130 transaction from any unprivileged sender — no special role or privilege is required to trigger the applier/authorizer's storage writes at the hardcoded `ACCOUNT_CONFIG` address. The only precondition is that the deployment step for `AccountConfiguration` did not actually occur at the pinned address on the chain in question (e.g., a new devnet/testnet/L2 enabling the fork), which the codebase does nothing to detect or prevent — it is asserted only in a code comment, with no corresponding force-deploy or genesis-validation safety net as exists for the other two EIP-8130/Zenith/Canyon system addresses.

### Recommendation
Extend `CODELESS_SYSTEM_ACCOUNTS`/`ensure_eip8130_system_accounts` (or an equivalent guard executed before the enshrined authorization/applier path runs) to also force-deploy (or hard-fail loudly at startup/fork activation) if `Eip8130Contracts::ACCOUNT_CONFIG` has no code, rather than relying on an unverified deployment assumption. Alternatively, add an explicit genesis/fork-activation invariant check that panics or refuses to activate Zenith if `AccountConfiguration`'s bytecode is not present at the pinned address, matching the pattern already used for `BaseTime::ensure_predeploy`'s `MissingProxy`/`CodelessProxy` checks.

### Proof of Concept
1. Configure a chain (e.g. a new Base devnet) that activates the Zenith/EIP-8130 upgrade timestamp without including the deployment of `AccountConfiguration` bytecode at `Eip8130Contracts::ACCOUNT_CONFIG` (address!("0x813012Bd8D971928475235BBac6F0488c4A100AC")) in its genesis or upgrade transactions.
2. Submit an ordinary EIP-8130 transaction (e.g. `authenticateActor`/actor-authorization change) from an unprivileged sender; `AccountChangeApplier`/`ActorAuthorizer` writes actor config directly into `AccountConfigurationStorage` slots at that address via the journal, without checking that code exists there (`crates/execution/eip8130/src/apply.rs`, `crates/execution/eip8130/src/authorize.rs`).
3. Because `ensure_eip8130_system_accounts` only stubs `NonceManagerStorage::ADDRESS` and explicitly skips `AccountConfiguration` (`crates/common/evm/src/zenith.rs:16-26`), the account at `ACCOUNT_CONFIG` remains EIP-161-empty (code empty, balance 0, nonce 0) despite holding freshly-written storage.
4. At end-of-block EIP-161 state clearing, the account is reaped, discarding the actor-authorization state that was just written — the user's signed configuration change is silently lost even though their transaction succeeded.

### Citations

**File:** crates/common/consensus/src/transaction/eip8130/addresses.rs (L47-50)
```rust
    /// Account Configuration system contract (`ACCOUNT_CONFIG_ADDRESS`). The
    /// protocol reads actor/account state directly from this contract's storage.
    pub const ACCOUNT_CONFIG: Address = address!("0x813012Bd8D971928475235BBac6F0488c4A100AC");

```

**File:** crates/execution/eip8130/src/account_config.rs (L42-46)
```rust
    /// Account Configuration system-contract address.
    ///
    /// Pinned to [`Eip8130Contracts::ACCOUNT_CONFIG`]; provisional and tracks the
    /// reference contract's bytecode (see the crate docs).
    pub const ADDRESS: Address = Eip8130Contracts::ACCOUNT_CONFIG;
```

**File:** crates/execution/eip8130/src/apply.rs (L1-21)
```rust
//! The EIP-8130 account-changes apply step: the state mutations the
//! [`ConfigChangeAuthorizer`] deliberately defers, plus account creation and
//! delegation, mirroring `AccountConfiguration`'s write semantics.
//!
//! [`ConfigChangeAuthorizer`] authenticates a signed batch and gates it on
//! admin scope (`scope == 0`), but does not decode each [`SignedChange`]'s
//! `payload` or mutate `actor_config`; that is this module's job. It is the
//! native mirror of `Keystore.applySignedAccountChanges`'s mutation tail
//! (`_applyAuthorize` / `_applyRevoke` / `_slicePolicy`), of `createAccount` /
//! `_initializeAccount`, and of the deterministic CREATE2 address derivation.
//!
//! Two effects of an account change touch the *account's code* rather than the
//! `AccountConfiguration` storage this crate owns — deploying a created
//! account's bytecode and writing an [EIP-7702]-style delegation indicator. The
//! applier performs every `AccountConfiguration` storage transition itself and
//! surfaces those code writes as an [`AppliedAccountChanges`] for the execution
//! layer (which holds the account/state-trie handle) to carry out.
//!
//! Successful authorize / revoke / create mutations also inject the matching
//! `IAccountConfiguration` receipt logs via [`AccountConfigurationEvents`]
//! (the enshrined path has no EVM LOG opcodes of its own).
```

**File:** crates/common/evm/src/zenith.rs (L16-26)
```rust
/// Code-less EIP-8130 system accounts that hold persistent storage but carry no
/// code on any chain, and therefore must be made non-empty so EIP-161
/// end-of-block state clearing does not reap them together with their storage.
///
/// Only the [`NonceManager`](NonceManagerStorage) qualifies: it persists the 2D
/// nonce channels in the state trie while never being a deployed contract. The
/// transaction-context precompile (`0x8130…aa02`) uses transient storage only
/// (cleared every transaction, never trie-resident) so it needs no stub, and
/// `AccountConfiguration` is a genuinely deployed contract (it carries code) on
/// every chain where EIP-8130 is enabled.
const CODELESS_SYSTEM_ACCOUNTS: [Address; 1] = [NonceManagerStorage::ADDRESS];
```

**File:** crates/common/evm/src/canyon.rs (L18-49)
```rust
/// The Canyon upgrade issues an irregular state transition that force-deploys the create2
/// deployer contract. This is done by directly setting the code of the create2 deployer account
/// prior to executing any transactions on the timestamp activation of the fork.
pub fn ensure_create2_deployer<DB>(
    chain_spec: impl Upgrades,
    timestamp: u64,
    db: &mut DB,
) -> Result<(), DB::Error>
where
    DB: Database + DatabaseCommit,
{
    // If the canyon upgrade is active at the current timestamp, and it was not active at the
    // previous block timestamp (heuristically, block time is not perfectly constant at 2s), and the
    // chain is a Base chain, then we need to force-deploy the create2 deployer contract.
    if chain_spec.is_canyon_active_at_timestamp(timestamp)
        && !chain_spec.is_canyon_active_at_timestamp(timestamp.saturating_sub(2))
    {
        // Load the create2 deployer account from the cache.
        let mut acc_info = db.basic(CREATE_2_DEPLOYER_ADDR)?.unwrap_or_default();

        // Update the account info with the create2 deployer codehash and bytecode.
        acc_info.code_hash = CREATE_2_DEPLOYER_CODEHASH;
        acc_info.code = Some(Bytecode::new_raw(Bytes::from_static(&CREATE_2_DEPLOYER_BYTECODE)));

        // Convert the cache account back into a revm account and mark it as touched.
        let mut revm_acc: revm::state::Account = acc_info.into();
        revm_acc.mark_touch();

        // Commit the create2 deployer account to the database.
        db.commit(HashMap::from_iter([(CREATE_2_DEPLOYER_ADDR, revm_acc)]));
        return Ok(());
    }
```

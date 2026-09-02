## Finding

The frontrunning pattern from the Audius report (H08) maps onto `StakingPoolFactory::create_staking_pool` in this repository. It reserves a global identifier (`staking_pool_account_id`) with no binding to `predecessor_account_id`, and unconditionally whitelists whatever gets created there.

### Title
Frontrunning `create_staking_pool` lets an attacker squat a service provider's chosen account name and get a maliciously-parameterised pool auto-whitelisted - (File: `staking-pool-factory/src/lib.rs`)

### Summary
`StakingPoolFactory::create_staking_pool` derives the new pool's account ID solely from the caller-supplied `staking_pool_id` string concatenated with the factory's own account ID, and reserves it synchronously in `self.staking_pool_account_ids` before the actual account-creation promise resolves. Because the identifier is never bound to `env::predecessor_account_id()`, an attacker who observes an honest party's pending `create_staking_pool` transaction in the mempool can resubmit the same `staking_pool_id` with their own `owner_id`, claim the account name first, and have their attacker-controlled pool automatically whitelisted by the factory's callback.

### Finding Description
`create_staking_pool` builds the child account name purely from attacker/user-controlled input: [1](#0-0) 

There is no hashing of `staking_pool_id` together with `msg.sender`/`predecessor_account_id`, unlike the commit/reveal binding recommended in the referenced report. `self.staking_pool_account_ids.insert(&staking_pool_account_id)` executes synchronously in the same call, so whichever transaction lands first "wins" the name — a pure ordering/frontrunning race, exactly the bug class described in the external report.

If the attacker's transaction lands first with the same `staking_pool_id` but their own `owner_id`, `stake_public_key`, and `reward_fee_fraction`, the promise chain proceeds to create the account, deploy the staking-pool contract, and — on success — unconditionally whitelist it: [2](#0-1) 

The honest party's subsequent transaction with the same `staking_pool_id` then reverts at the `insert` assertion: [3](#0-2) 

The `WhitelistContract::add_staking_pool` call from the factory trusts any account ID reported as successfully created, with no verification of who actually owns/controls it: [4](#0-3) 

The binding broken is: *the account name a delegator trusts as belonging to a specific service/validator operator* versus *the code and constructor arguments (`owner_id`, `stake_public_key`) that actually control that whitelisted account*. After the race, the whitelisted pool at the expected name is parameterised entirely by the attacker.

### Impact Explanation
This is a "wrongly whitelisted or wrongly parameterised deployment" as described by the rules' Critical impact category: the resulting on-chain, whitelisted staking pool at the name the honest operator intended is deployed with `owner_id` set to the attacker rather than the honest operator. Any delegator who identifies the pool by its expected/advertised account name (which is the normal way delegators pick a validator to stake with) will deposit and stake NEAR into a pool the attacker fully controls (attacker can change the staking key, adjust `reward_fee_fraction`, and otherwise administer the pool as owner), while the honest operator's registration attempt permanently reverts for that name (`"The staking pool account ID already exists"`).

### Likelihood Explanation
The attack requires only observing a pending `create_staking_pool` transaction and resubmitting with a higher gas price/priority and the minimum attached deposit `MIN_ATTACHED_BALANCE` (30 NEAR): [5](#0-4) 
There is no economic disincentive analogous to slashing (unlike the staking contract in the original report) — the attacker's only cost is the deposit, which is spent creating a functioning, whitelisted pool they fully control, so the "cost" is not pure loss. This mirrors the same "low/unclear cost, mempool-visible" conditions that caused the original report to be rated High/Critical.

### Recommendation
Bind the created account identifier to the caller, e.g. by hashing `staking_pool_id` together with `env::predecessor_account_id()` (or requiring `owner_id == predecessor_account_id()`), so an attacker cannot register a name intended for another account. Alternatively, require a commit/reveal step (commit to `sha256(staking_pool_id, predecessor_account_id)`, wait a delay, then reveal) before the account-creation promise is dispatched, analogous to the fix direction described in the referenced report.

### Proof of Concept
1. Honest operator Alice broadcasts `create_staking_pool("audius1", "alice.near", alice_pubkey, fee)` with 30+ NEAR attached.
2. Attacker observes this in the mempool and immediately submits `create_staking_pool("audius1", "attacker.near", attacker_pubkey, fee)` with a higher gas price, so it is included first.
3. Attacker's call succeeds: `staking_pool_account_ids.insert("audius1.factory.near")` succeeds, the account `audius1.factory.near` is created and deployed with `owner_id = "attacker.near"`, and `on_staking_pool_create` whitelists it via `ext_whitelist::add_staking_pool`.
4. Alice's transaction now executes and reverts at `assert!(self.staking_pool_account_ids.insert(...), "The staking pool account ID already exists")`.
5. Delegators who intended to stake with Alice's advertised pool name `audius1.factory.near` instead deposit into a whitelisted pool fully controlled by the attacker.

### Citations

**File:** staking-pool-factory/src/lib.rs (L10-11)
```rust
/// The 30 NEAR tokens required for the storage of the staking pool.
const MIN_ATTACHED_BALANCE: Balance = 30_000_000_000_000_000_000_000_000;
```

**File:** staking-pool-factory/src/lib.rs (L154-170)
```rust
        let staking_pool_account_id = format!("{}.{}", staking_pool_id, env::current_account_id());
        assert!(
            env::is_valid_account_id(staking_pool_account_id.as_bytes()),
            "The staking pool account ID is invalid"
        );

        assert!(
            env::is_valid_account_id(owner_id.as_bytes()),
            "The owner account ID is invalid"
        );
        reward_fee_fraction.assert_valid();

        assert!(
            self.staking_pool_account_ids
                .insert(&staking_pool_account_id),
            "The staking pool account ID already exists"
        );
```

**File:** staking-pool-factory/src/lib.rs (L197-224)
```rust
    /// Callback after a staking pool was created.
    /// Returns the promise to whitelist the staking pool contract if the pool creation succeeded.
    /// Otherwise refunds the attached deposit and returns `false`.
    pub fn on_staking_pool_create(
        &mut self,
        staking_pool_account_id: AccountId,
        attached_deposit: U128,
        predecessor_account_id: AccountId,
    ) -> PromiseOrValue<bool> {
        assert_self();

        let staking_pool_created = is_promise_success();

        if staking_pool_created {
            env::log(
                format!(
                    "The staking pool @{} was successfully created. Whitelisting...",
                    staking_pool_account_id
                )
                .as_bytes(),
            );
            ext_whitelist::add_staking_pool(
                staking_pool_account_id,
                &self.staking_pool_whitelist_account_id,
                NO_DEPOSIT,
                gas::WHITELIST_STAKING_POOL,
            )
            .into()
```

**File:** whitelist/src/lib.rs (L75-88)
```rust
    pub fn add_staking_pool(&mut self, staking_pool_account_id: AccountId) -> bool {
        assert!(
            env::is_valid_account_id(staking_pool_account_id.as_bytes()),
            "The given account ID is invalid"
        );
        // Can only be called by a whitelisted factory or by the foundation.
        if !self
            .factory_whitelist
            .contains(&env::predecessor_account_id())
        {
            self.assert_called_by_foundation();
        }
        self.whitelist.insert(&staking_pool_account_id)
    }
```

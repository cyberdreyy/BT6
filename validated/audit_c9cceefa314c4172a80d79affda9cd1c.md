## Analog Found: Duplicate Members Bypass `num_confirmations` Threshold Check in Multisig Initialization

### Title
Duplicate multisig members in `new()` let a deployer set an unreachable confirmation threshold, permanently freezing multisig funds - (File: `multisig2/src/lib.rs`)

### Summary
The `MultiSigContract::new` initializer validates `num_confirmations` against the length of the raw input `Vec<MultisigMember>`, but stores members in a `UnorderedSet` that silently deduplicates. If the input array contains duplicate entries, the length check passes against the inflated count while the actual number of distinct members stored is lower, allowing `num_confirmations` to be set above the number of members that can ever confirm a request — permanently freezing the account.

### Finding Description
In `new()`, the threshold check is performed before insertion, against the raw `Vec` length: [1](#0-0) 

```rust
pub fn new(members: Vec<MultisigMember>, num_confirmations: u32) -> Self {
    assert(
        members.len() >= num_confirmations as usize,
        "Members list must be equal or larger than number of confirmations",
    );
    ...
    for member in members {
        promise = multisig.add_member(promise, member);
    }
    multisig
}
```

`add_member` inserts each member into `self.members`, which is an `UnorderedSet<MultisigMember>`: [2](#0-1) 

`UnorderedSet::insert` deduplicates on `MultisigMember` equality (`PartialEq` derived from `AccessKey{public_key}` / `Account{account_id}`), so passing the same member twice (or more) in the `members` vector results in `self.members.len()` being strictly smaller than the `members.len()` used in the initial assert. This is exactly the bug class described in the report: the loop that populates the committee/member storage never checks for duplicates, creating a discrepancy between the declared count and actual distinct member count used to gate the `num_confirmations` threshold.

Contrast this with `delete_member`, which correctly uses the live set length for the same invariant: [3](#0-2) 

```rust
assert(
    self.members.len() - 1 >= self.num_confirmations as u64,
    "Removing given member will make total number of members below number of confirmations",
);
```

This shows the codebase's intended invariant is "live distinct member count >= num_confirmations", but `new()` fails to enforce it against the deduplicated set.

### Impact Explanation
If `num_confirmations` ends up greater than the number of distinct stored members (e.g., 4 entries supplied with 2 duplicates, `num_confirmations = 3`, but only 2 unique members actually stored), `confirm()` can never reach the required threshold: [4](#0-3) 

Since each distinct member can only confirm once (`assert(!confirmations.contains(&member.to_string())...)`), and the maximum possible confirmations equals the number of distinct members, any request (including `Transfer` or `SetNumConfirmations` needed to recover) becomes permanently unexecutable. Because the multisig account itself must approve `SetNumConfirmations` through the same broken threshold, there is no recovery path — this is a **Critical** impact: funds sent to/held by this multisig are permanently frozen.

### Likelihood Explanation
The multisig account is typically created via `multisig-factory`'s `create()` entry point, which forwards attacker/caller-supplied `members` and `num_confirmations` directly into `new()` without any dedup or validation: [5](#0-4) 

Any caller of `create()` can trigger this by simply repeating one member's `public_key`/`account_id` in the `members` array while setting `num_confirmations` to the (inflated) array length. Given how easy it is to accidentally or deliberately supply duplicates (e.g., copy-paste errors in tooling, or a malicious deployer setting up a multisig for others), likelihood is non-trivial once the vulnerable pattern is understood.

### Recommendation
In `new()`, validate the threshold against the deduplicated storage, not the raw input length — e.g., insert all members first, then assert `multisig.members.len() >= num_confirmations as u64`, or explicitly reject duplicate entries in the input vector before insertion (mirroring the fix pattern from the referenced Tidal Pool contract fix, which added an explicit "already added" check before pushing each member).

### Proof of Concept
1. Call `multisig-factory::create` (or `multisig2::new` directly) with:
   - `members = [{"account_id": "alice"}, {"account_id": "alice"}, {"account_id": "bob"}]` (3 entries, 2 duplicates of "alice")
   - `num_confirmations = 3`
2. The assert `members.len() (3) >= num_confirmations (3)` passes.
3. After the loop, `self.members` (an `UnorderedSet`) contains only 2 distinct entries: `alice`, `bob`.
4. `get_num_confirmations()` returns 3, but the maximum achievable confirmations is 2 (alice + bob), since each member can confirm only once.
5. Any subsequent `add_request` + `confirm` sequence — including attempts to call `SetNumConfirmations` to fix the threshold — can never reach 3 confirmations, permanently locking any NEAR balance held by the account.

### Citations

**File:** multisig2/src/lib.rs (L147-167)
```rust
    #[init]
    pub fn new(members: Vec<MultisigMember>, num_confirmations: u32) -> Self {
        assert(
            members.len() >= num_confirmations as usize,
            "Members list must be equal or larger than number of confirmations",
        );
        let mut multisig = Self {
            members: UnorderedSet::new(StorageKeys::Members),
            num_confirmations,
            request_nonce: 0,
            requests: UnorderedMap::new(StorageKeys::Requests),
            confirmations: LookupMap::new(StorageKeys::Confirmations),
            num_requests_pk: LookupMap::new(StorageKeys::NumRequestsPk),
            active_requests_limit: ACTIVE_REQUESTS_LIMIT,
        };
        let mut promise = Promise::new(env::current_account_id());
        for member in members {
            promise = multisig.add_member(promise, member);
        }
        multisig
    }
```

**File:** multisig2/src/lib.rs (L292-315)
```rust
    /// Confirm given request with given signing key.
    /// If with this, there has been enough confirmation, a promise with request will be scheduled.
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let member = self
            .current_member()
            .unwrap_or_else(|| env::panic_str("Must be validated above"));
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert(
            !confirmations.contains(&member.to_string()),
            "Already confirmed this request with this key",
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(member.to_string());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
    }
```

**File:** multisig2/src/lib.rs (L341-353)
```rust
    /// Add member to the list. Adds access key if member is key based.
    fn add_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        self.members.insert(&member.clone().into());
        match member {
            MultisigMember::AccessKey { public_key } => promise.add_access_key(
                public_key.into(),
                DEFAULT_ALLOWANCE,
                env::current_account_id(),
                MULTISIG_METHOD_NAMES.to_string(),
            ),
            MultisigMember::Account { account_id: _ } => promise,
        }
    }
```

**File:** multisig2/src/lib.rs (L356-360)
```rust
    fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        assert(
            self.members.len() - 1 >= self.num_confirmations as u64,
            "Removing given member will make total number of members below number of confirmations",
        );
```

**File:** multisig-factory/src/lib.rs (L28-49)
```rust
    #[payable]
    pub fn create(
        &mut self,
        name: AccountId,
        members: Vec<MultisigMember>,
        num_confirmations: u64,
    ) -> Promise {
        let account_id = format!("{}.{}", name, env::current_account_id());
        Promise::new(account_id)
            .create_account()
            .deploy_contract(CODE.to_vec())
            .transfer(env::attached_deposit())
            .function_call(
                b"new".to_vec(),
                json!({ "members": members, "num_confirmations": num_confirmations })
                    .to_string()
                    .as_bytes()
                    .to_vec(),
                0,
                env::prepaid_gas() - CREATE_CALL_GAS,
            )
    }
```

## Title
Removing a multisig member/key doesn't purge their prior confirmations on other pending requests, allowing a request to execute with fewer live approvals than `num_confirmations` - (File: `multisig2/src/lib.rs`, also `multisig/src/lib.rs`)

### Summary
This is a structural analog of the RIPEMD160 bug: state (`start`, the original dict pointer) is not updated/invalidated after the underlying data it referenced was replaced, so a later "finalize" step (`default_dict_finalize`) operates on stale/inconsistent state. In the multisig contracts, when a member (or access key) is removed, the contract only purges the **requests that member created**, but never purges the **confirmations that member previously cast on other members' pending requests**. Those stale confirmations remain in the `confirmations` set and are still counted toward `num_confirmations` when a later `confirm()` call checks the threshold, breaking the binding between "confirmations counted" and "live members."

### Finding Description
In `multisig2/src/lib.rs`, `delete_member` only cleans up requests created by the removed member: [1](#0-0) 

It filters `self.requests` for entries where `r.member == member` (i.e., requests the member *authored*) and removes those, along with their confirmation sets. It does **not** scan other pending requests' `confirmations` sets to strip out the entry belonging to the removed member. `self.members.remove(&member)` removes them from the live-member set, but their prior `confirmations.insert(member.to_string())` calls on other pending requests remain untouched.

Later, `confirm()` computes the threshold purely from the cardinality of the stored confirmation set, without checking whether each entry in that set still corresponds to a live member: [2](#0-1) 

So `confirmations.len() as u32 + 1 >= self.num_confirmations` can be satisfied by counting a confirmation from an account that is no longer a member.

The same pattern exists in the legacy `multisig/src/lib.rs`, where `DeleteKey` only removes requests where `r.signer_pk == pk` (i.e., authored by that key), leaving confirmations that key gave on other pending requests intact: [3](#0-2) 

and `confirm()` uses the same unfiltered cardinality check: [4](#0-3) 

The binding that should hold is:
```
confirmations_counted(request_id) == confirmations_from_currently_live_members(request_id)
```
This is violated once a confirming member/key is removed after confirming but before the request reaches quorum and executes.

### Impact Explanation
This breaks the "confirmations counted versus live members" custody binding explicitly called out as in-scope. A request (e.g., a `Transfer` action moving NEAR out of the multisig account) can be executed by `execute_request` with fewer *currently authorized* approvals than `num_confirmations` mandates, because a stale confirmation from a removed member/key still counts. This matches the Critical impact category "a multisig request executed below threshold," since assets held by the multisig can be moved (or arbitrary `FunctionCall`/`AddKey`/`DeployContract` actions executed) without the intended quorum of live signers.

### Likelihood Explanation
This requires no attacker-controlled code changes, no owner/foundation cooperation beyond the ordinary lifecycle of multisig membership: members leave organizations, keys are rotated, or a compromised key is deliberately removed - all routine, expected operations. Any request left pending (not yet at quorum) at the time a confirming member is removed will retain the stale confirmation. The remaining members do not need to intend this outcome; it happens automatically the next time `confirm()` is called on that request. Given that membership changes and pending requests are both normal usage patterns, likelihood is meaningful, not purely theoretical.

### Recommendation
When removing a member/key (`delete_member` in `multisig2`, `DeleteKey` handling in `multisig`), iterate over **all** pending requests' confirmation sets (not just requests authored by that member) and strip the removed member's/key's entry from each. Alternatively, validate at `confirm()` time that every entry in the stored confirmation set still corresponds to a current member before counting it toward the threshold.

### Proof of Concept
Using `multisig2::MultiSigContract`:
1. Initialize with `members = [X, Y, Z]`, `num_confirmations = 3` (see `new` at [5](#0-4) ).
2. `X` calls `add_request` with a `Transfer` action to an attacker-controlled account (no auto-confirm).
3. `Y` calls `confirm(request_id)` → `confirmations = {Y}`.
4. `Z` calls `confirm(request_id)` → `confirmations = {Y, Z}` (still below threshold of 3, so it is not executed yet).
5. Separately, the multisig legitimately executes a `DeleteMember { member: Y }` request (e.g., `Y`'s key was compromised or `Y` left the organization) via `delete_member` at [1](#0-0) . Because this only filters requests *authored by* `Y`, the pending transfer request (authored by `X`) keeps `confirmations = {Y, Z}` untouched.
6. `X` now calls `confirm(request_id)`. In `confirm`, `confirmations.len() as u32 + 1` = `2 + 1` = `3 >= self.num_confirmations (3)` → the request executes at [6](#0-5) .
7. The transfer is executed with only `X` and `Z` as actual live approvers plus one stale confirmation from the removed member `Y` — i.e., the request executed with only 2 live confirmations against a nominal 3-of-n threshold.

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

**File:** multisig2/src/lib.rs (L356-379)
```rust
    fn delete_member(&mut self, promise: Promise, member: MultisigMember) -> Promise {
        assert(
            self.members.len() - 1 >= self.num_confirmations as u64,
            "Removing given member will make total number of members below number of confirmations",
        );
        // delete outstanding requests by public_key
        let request_ids: Vec<u32> = self
            .requests
            .iter()
            .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
            .collect();
        for request_id in request_ids {
            // remove confirmations for this request
            self.confirmations.remove(&request_id);
            self.requests.remove(&request_id);
        }
        // remove num_requests_pk entry for member
        self.num_requests_pk.remove(&member.to_string());
        self.members.remove(&member);
        match member {
            MultisigMember::AccessKey { public_key } => promise.delete_key(public_key.into()),
            MultisigMember::Account { account_id: _ } => promise,
        }
    }
```

**File:** multisig/src/lib.rs (L198-216)
```rust
                MultiSigRequestAction::DeleteKey { public_key } => {
                    self.assert_self_request(receiver_id.clone());
                    let pk: PublicKey = public_key.into();
                    // delete outstanding requests by public_key
                    let request_ids: Vec<u32> = self
                        .requests
                        .iter()
                        .filter(|(_k, r)| r.signer_pk == pk)
                        .map(|(k, _r)| k)
                        .collect();
                    for request_id in request_ids {
                        // remove confirmations for this request
                        self.confirmations.remove(&request_id);
                        self.requests.remove(&request_id);
                    }
                    // remove num_requests_pk entry for public_key
                    self.num_requests_pk.remove(&pk);
                    promise.delete_key(pk)
                }
```

**File:** multisig/src/lib.rs (L246-266)
```rust
    /// Confirm given request with given signing key.
    /// If with this, there has been enough confirmation, a promise with request will be scheduled.
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert!(
            !confirmations.contains(&env::signer_account_pk()),
            "Already confirmed this request with this key"
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(env::signer_account_pk());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
    }
```

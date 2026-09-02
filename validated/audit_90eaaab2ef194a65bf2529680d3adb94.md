Confirmed in both `multisig/src/lib.rs` and `multisig2/src/lib.rs`: when a signer/member is removed via `DeleteKey`/`DeleteMember`, only requests *created by* that key/member are purged; stale confirmations that key/member left on *other* pending requests (created by someone else) are never cleaned up. This is the exact analog of the ERC20Boost bug: a recorded credential (`confirmations` entry) survives the removal of the underlying entity (access key / member) and later counts toward the confirmation threshold even though the confirmer is no longer authorized — "confirmations counted versus live members" diverging.

### Title
Stale confirmations from deleted multisig keys/members count toward execution threshold, allowing requests to execute below the required number of live confirmations - (File: multisig/src/lib.rs, multisig2/src/lib.rs)

### Summary
`DeleteKey` (multisig v1) and `DeleteMember` (multisig2) only purge requests *created by* the removed key/member, but never scrub that key/member's *confirmations* left on other pending requests. A confirmation given by a since-removed signer remains in the `confirmations` set and is counted by `confirm()` when checking `confirmations.len() + 1 >= num_confirmations`, letting a request execute with fewer live, currently-authorized confirmations than the configured threshold.

### Finding Description
In `multisig/src/lib.rs`, the `DeleteKey` action handler filters and removes only requests where `r.signer_pk == pk` (requests *added by* that key): [1](#0-0) 

It never removes entries of `pk` from `self.confirmations` on requests created by other keys. The same pattern exists in `multisig2/src/lib.rs`'s `delete_member`, which filters `r.member == member` (requests created by that member) before deleting, again leaving stale confirmations on other requests untouched: [2](#0-1) 

`confirm()` in both versions simply checks the *current* signer/member isn't already in the confirmation set for duplicate prevention, and then compares `confirmations.len() as u32 + 1 >= self.num_confirmations` to decide whether to execute the request — it never re-validates that the previously recorded confirmers are still members: [3](#0-2) [4](#0-3) 

This breaks the invariant `confirmations counted == confirmations from live members`. A confirmation cast by member B before B is removed silently continues to count as "1 of K" forever, effectively lowering the live threshold by however many stale confirmations accumulate across removed members.

### Impact Explanation
This is Critical per the given impact categories ("a multisig request executed below threshold"). Example: multisig2 with members {A, B, C, D}, `num_confirmations = 3`. Member B confirms `RequestX` (created by A) → confirmations = {B}. Governance later removes B via `DeleteMember` (a separate, valid K-of-N action) — `delete_member` only clears requests *created by* B, so `RequestX`'s confirmation set still contains B. Now only C and D remain besides A/creator; C confirms (`len+1=2`), D confirms (`len+1=3 >= 3`) → `RequestX` executes, even though only 2 of the 3 confirmations came from currently-live members. The multisig's documented K-of-N security guarantee is silently downgraded, and an attacker/colluding subset smaller than K can push through transfers, key additions, or contract upgrades.

### Likelihood Explanation
No special privileges beyond normal multisig operation are required to trigger this: any member can confirm a request before being removed in the ordinary course of membership rotation (a routine, expected admin operation, not a malicious deployment or ignored initialization). Any project that rotates members/keys over time (which the contract explicitly supports via `DeleteKey`/`DeleteMember`) will accumulate stale confirmations, making this reachable through completely standard usage rather than a contrived edge case.

### Recommendation
When executing `DeleteKey`/`DeleteMember`, iterate over **all** pending requests' `confirmations` sets (not just the ones the removed key/member created) and remove any entry matching the removed key/member. Alternatively, at `confirm()` time, re-validate every entry in the stored confirmation set against `self.members` (or currently valid access keys) before counting it toward the threshold, discarding stale entries.

### Proof of Concept
1. Initialize `multisig2` with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `A.add_request(RequestX)` → `RequestX` created, `confirmations = {}`.
3. `B.confirm(RequestX)` → `confirmations = {B}` (1 of 3).
4. Members execute a separate `DeleteMember { member: B }` request (via its own 3-of-4 confirmation) — `delete_member` removes B from `self.members` and deletes any requests *created by* B, but leaves `RequestX`'s `confirmations = {B}` untouched (`multisig2/src/lib.rs:361-371`).
5. `C.confirm(RequestX)` → `confirmations.len()+1 = 2 >= 3`? No → `confirmations = {B, C}`.
6. `D.confirm(RequestX)` → `confirmations.len()+1 = 3 >= 3` → `execute_request` runs `RequestX` even though B is no longer a member; only C and D are live confirmers, i.e., the request executed with 2 live confirmations instead of the required 3.

### Citations

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

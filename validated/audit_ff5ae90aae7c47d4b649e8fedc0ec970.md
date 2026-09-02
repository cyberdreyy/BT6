### Title
Stale confirmations from removed members counted toward `num_confirmations`, allowing requests to execute below the live-member threshold - ([File: multisig2/src/lib.rs])

### Summary
`delete_member` (multisig2) and the `DeleteKey` action handler (multisig) remove a departing member's *own* pending requests and `num_requests_pk` entry, but never scrub that member's already-recorded confirmations on *other* still-pending requests. Those stale confirmations remain in the `confirmations` set and continue to count toward `num_confirmations` in `confirm()`, so a request can later execute with confirmations from members who are no longer live/authorized, breaking the "confirmations counted versus live members" binding that this class of bug is about.

### Finding Description
`confirm()` only checks the *count* of entries in the `confirmations` `HashSet` against `num_confirmations`: [1](#0-0) 

When a member is removed via the `DeleteMember` action, `delete_member` purges requests that member *originated*, removes their `num_requests_pk` entry, and removes them from the `members` set — but it does not walk `self.confirmations` to strip that member's prior confirmation entries from requests originated by *other* members: [2](#0-1) 

The same gap exists in the legacy `multisig` contract: `DeleteKey` only removes requests where `r.signer_pk == pk` (i.e., requests added by that key), leaving that key's confirmations on other requests untouched: [3](#0-2) 

`assert_valid_request` (called at the top of `confirm`) only checks that the caller is a current member and that the request/confirmations map entries exist — it never re-validates that the *existing* confirmations still belong to current members: [4](#0-3) 

The binding that should hold is: `count(confirmations for request X) == count(live members who confirmed X)`. Because removal does not scrub stale entries, this equality is broken — a confirmation recorded by a member who has since been deleted still counts toward the threshold.

### Impact Explanation
This is the same bug class as the reported H-2 (`reconcileSignerCount`/stale-count divergence): a threshold check based on a stale accounting number rather than the live set of authorized parties. Here the consequence is the inverse (and more severe) direction: a `MultiSigRequest` — which can transfer NEAR, add a full-access key, deploy new code, or delete/add members — can be executed with fewer than `num_confirmations` *currently live* member approvals, because a confirmation from an already-removed member is still tallied. This matches the "Critical: a multisig request executed below threshold" impact category, since arbitrary funds transfers, key additions, or contract upgrades can be authorized without the intended quorum of currently-trusted members.

### Likelihood Explanation
This requires no external/foundation intervention and no compromised keys: it occurs through the multisig's own normal, documented workflow (a pending request gets partial confirmations, and separately a member is removed via a routine `DeleteMember`/`DeleteKey` request) — an ordinary operational sequence (e.g., off-boarding a departing team member) rather than a contrived attack precondition. Any of the remaining members can simply continue confirming the older pending request to trigger execution below the real, current-member threshold.

### Recommendation
When executing `DeleteMember` (`multisig2`) or `DeleteKey` (`multisig`), iterate over `self.confirmations` for **all** pending requests (not just ones authored by the removed member/key) and remove the removed member's entry from each confirmation set. Alternatively, validate at `confirm()`-time that all recorded confirmations for a request still correspond to current `members` before counting them toward `num_confirmations`.

### Proof of Concept
1. Initialize `multisig2` with `members = [A, B, C, D]`, `num_confirmations = 3`.
2. `A.add_request(X)` — e.g. `Transfer` to some receiver. `confirmations[X] = {}`.
3. `B.confirm(X)` → `len()+1 = 1 < 3` → `confirmations[X] = {B}`.
4. `C.confirm(X)` → `len()+1 = 2 < 3` → `confirmations[X] = {B, C}`.
5. Separately, members confirm a `DeleteMember{member: C}` request; `delete_member` runs, checks `members.len()-1 (=3) >= num_confirmations (=3)` (passes), removes `C` from `members`, but `confirmations[X]` is untouched — still `{B, C}`.
6. `D.confirm(X)` → `confirmations[X].len()+1 = 3 >= 3` → request `X` executes.
7. Result: `X` (e.g. a `Transfer`) executed with confirmations `{B, C, D}` counted, but `C` is no longer a member — only `B` and `D` are actually live, authorized confirmers (2 of 4 current members, i.e., below the configured 3-of-N threshold with live signers).

### Citations

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

**File:** multisig2/src/lib.rs (L355-379)
```rust
    /// Delete member from the list. Removes access key if the member is key based.
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

**File:** multisig2/src/lib.rs (L406-423)
```rust
    /// Prevents access to calling requests and make sure request_id is valid - used in delete and confirm
    fn assert_valid_request(&mut self, request_id: RequestId) {
        // request must come from key added to contract account
        assert(
            self.current_member().is_some(),
            "Caller (predecessor or signer) is not a member of this multisig",
        );
        // request must exist
        assert(
            self.requests.get(&request_id).is_some(),
            "No such request: either wrong number or already confirmed",
        );
        // request must have
        assert(
            self.confirmations.get(&request_id).is_some(),
            "Internal error: confirmations mismatch requests",
        );
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

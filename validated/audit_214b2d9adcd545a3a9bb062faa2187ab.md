### Title
Stale confirmations from removed multisig members still count toward the confirmation threshold, allowing requests to execute below the live-member threshold - (File: `multisig2/src/lib.rs`, also present in `multisig/src/lib.rs`)

### Summary
`delete_member()` in the multisig2 contract only purges pending *requests* originated by the removed member, but never scans the `confirmations` map to strip that member's confirmation entries from *other* pending requests they had previously confirmed. A confirmation cast by a member who is later removed continues to be counted toward `num_confirmations`, letting a request execute using fewer live-member approvals than the configured threshold.

### Finding Description
The contract's core invariant is: a request executes only once `num_confirmations` distinct *current members* have confirmed it, checked in `confirm()`: [1](#0-0) 

Confirmations are stored per-request as a `HashSet<String>` keyed by the member's serialized identity, independent of whether that identity is still in `self.members`. When a member is removed via `DeleteMember`, `delete_member()` only deletes requests that member *originated* (`r.member == member`), and removes their `num_requests_pk` counter and multisig membership/access key — but never removes their prior confirmation entries from `self.confirmations` for any *other* request they had confirmed but not created: [2](#0-1) 

So if member `C` confirms request `X` (created by someone else, or by `C` themself if not immediately executed) and is later removed from the multisig, `X`'s confirmation set in `self.confirmations` still contains `C`'s stale confirmation. `assert_valid_request()` and `confirm()` never re-validate that stored confirmers are still members: [3](#0-2) 

This exactly matches the bug class in the report: a per-item cap/threshold check (`MAXIMUM_NUMBER_OF_DEPOSITS_PER_ROUND` vs. `confirmations.len() >= num_confirmations`) is satisfied using state that was valid at insertion time but is no longer valid at evaluation time, because a state transition (unpause / member removal) is not propagated to already-recorded entries.

The identical pattern exists in the older `multisig/src/lib.rs`: `DeleteKey` only clears requests where `r.signer_pk == pk` (line 198-212) but never scans the `confirmations` map for stale entries from that key on other requests. [4](#0-3) 

### Impact Explanation
This breaks the "confirmations counted versus live members" binding explicitly protected elsewhere by `delete_member`'s own guard (`self.members.len() - 1 >= self.num_confirmations`), which is meant to guarantee `num_confirmations` live members are always obtainable. Because stale confirmations are not purged, a request can be executed (transferring funds, deploying code, adding a full-access key, etc.) with fewer *live* member approvals than `num_confirmations` requires — i.e. "a multisig request executed below threshold." Given the multisig can hold and transfer NEAR/fungible assets and manage access keys, this can lead to unauthorized fund movement or unauthorized privileged actions (`AddKey`, `AddMember`) approved by fewer active members than the security model guarantees.

### Likelihood Explanation
Member removal is a normal operational event (e.g., rotating a compromised key/account), and it is common for a request to be partially confirmed (by the member being removed, among others) at the time of removal. No malicious collusion beyond ordinary contract usage is required to reach the vulnerable state — only that a member removal occurs while an unrelated request that member previously confirmed is still pending.

### Recommendation
In `delete_member()` (and the analogous `DeleteKey` path in `multisig/src/lib.rs`), iterate over `self.confirmations` for all active requests and remove the deleted member's/key's entry from every confirmation set, not just requests it originated. Alternatively, validate at `confirm()`/execution time that every entry in a request's `confirmations` set still corresponds to a current member before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy multisig2 with members `A`, `B`, `C` and `num_confirmations = 2`.
2. `C` calls `add_request` to create transfer request `X` to itself (not yet confirmed by 2, so it stays pending), then calls `confirm(X)` — `X.confirmations = {C}` (1 of 2).
3. Separately, `A` and `B` submit and confirm a `DeleteMember { member: C }` request (reaches 2 confirmations, executes) — `C` is removed from `self.members`; only requests originated by `C` are purged; `X` and its confirmation set `{C}` remain untouched.
4. `A` now calls `confirm(X)`. `confirmations.len() as u32 + 1 = 2 >= num_confirmations(2)` is true, so `X` executes — funds are transferred even though only 1 live member (`A`) ever approved it, not 2 as the configured threshold requires.

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

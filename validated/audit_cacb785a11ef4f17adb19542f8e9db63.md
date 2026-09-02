Confirmed: `multisig2/src/lib.rs` has the identical gap — `delete_member` (lines 356-379) only purges requests *created by* the removed member (filters `r.member == member`), and never scans `self.confirmations` maps of *other* still-pending requests to strip out this member's stale confirmation entries. `confirm()` (lines 292-315) trusts the raw `confirmations.len()` regardless of whether every entry corresponds to a still-current member.

### Title
Stale confirmations from removed multisig members are still counted toward the approval threshold - (File: `multisig/src/lib.rs`, `multisig2/src/lib.rs`)

### Summary
The multisig contracts allow a request to execute once `confirmations.len() + 1 >= num_confirmations`. When a key/member is removed via `DeleteKey`/`DeleteMember`, the contract only deletes requests that were *created* by the removed key (`r.signer_pk == pk` in `multisig/src/lib.rs`, `r.member == member` in `multisig2/src/lib.rs`), but never removes that key's *confirmation entries* it left on other, still-open requests. Those stale confirmations continue to count toward the threshold indefinitely.

### Finding Description
`execute_request`'s `DeleteKey` branch [1](#0-0)  filters and removes only requests whose original *signer* (creator) matches the deleted public key, and clears `num_requests_pk` for that key. It does not iterate `self.confirmations` to strip the deleted key from confirmation sets of requests created by *other* members that this key had merely co-signed.

The equivalent `delete_member` in `multisig2` behaves the same way [2](#0-1) : it removes requests created by the departing member, checks that `members.len() - 1 >= num_confirmations`, and calls `promise.delete_key`, but likewise never scrubs the member's confirmation from other pending requests' `confirmations` sets.

`confirm()` in both contracts blindly trusts the stored confirmation set size: it never re-validates that each entry in `confirmations` still corresponds to a live key/member: [3](#0-2)  and [4](#0-3) .

This breaks the intended equality that the design assumes: `count of confirmations == count of currently-authorized signers who approved`. In reality, once a member is removed, `count of confirmations >= count of currently-authorized signers who approved`, because stale entries from removed members are never purged.

### Impact Explanation
This falls under the Critical category "a multisig request executed below threshold." A pending high-value request (e.g., a `Transfer` or `AddKey`/`FunctionCall`) that was co-signed by a member who is subsequently removed can later be pushed over the confirmation threshold using fewer *currently live* approvals than `num_confirmations` requires, because the stale confirmation from the removed member is still counted. This lets an attacker (or a subset of remaining signers below the policy threshold) execute an arbitrary multisig action — including transferring NEAR out of the account — with fewer real approvals than the k-of-n policy mandates.

### Likelihood Explanation
This is triggerable with only unprivileged actions available to normal multisig participants: create a request, have it partially confirmed by a member who is later removed (removal is a routine multisig operation, e.g. offboarding an employee or rotating a compromised key), then have the remaining members confirm to cross the threshold. No malicious validator, foundation action, or out-of-scope privilege is required — only ordinary multisig lifecycle operations (add key/member, confirm, delete key/member), all reachable via the contract's own public API in `multisig/src/lib.rs` and `multisig2/src/lib.rs`.

### Recommendation
When deleting a key/member (`DeleteKey`/`DeleteMember`), iterate over all entries in `self.confirmations` (not just requests created by that signer) and remove the deleted key/member from every confirmation set. Alternatively, validate at `confirm()`/execution time that every public key/member in a request's confirmation set is still present in the current key/member list before counting it toward the threshold.

### Proof of Concept
1. Initialize `multisig2` with 4 members `A, B, C, D` and `num_confirmations = 3`.
2. `A` calls `add_request` for a `Transfer` action to an attacker-controlled account (request id `X`).
3. `B` calls `confirm(X)` → `confirmations = {A? , B}` is not yet 3, request stays open.
4. Separately, members create and execute a `DeleteMember { member: B }` request (using `A`, `C`, `D` to reach the 3-of-4 threshold, since `delete_member` only checks `self.confirmations`/`self.requests` for requests *created by* `B`, `X` is untouched). `B`'s access key is deleted from the account and `B` is removed from `self.members`; `X`'s confirmation set still contains `B`.
5. `C` calls `confirm(X)`. `confirmations.len()` is now 2 (`A`/`B`… depending on exact flow) `+ 1 = 3 >= num_confirmations (3)`, so `execute_request` runs the `Transfer`, even though only 2 *currently valid* members (`A` and `C`) actually approved it out of the required 3-of-4 policy — `B`'s stale confirmation was counted despite `B` no longer being a member. [2](#0-1) [4](#0-3) [1](#0-0) [5](#0-4)

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

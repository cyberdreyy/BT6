Confirmed: I have enough to present the finding.

### Title
Multisig request executes below the required K-of-N threshold because stale confirmations from removed members are never purged - ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::confirm` in `multisig2/src/lib.rs` counts confirmations solely by the length of the `confirmations` set stored per request, without checking that every public key/account in that set is still a current member. `delete_member` only purges requests that were *created* by the removed member; it does not scan and strip that member's confirmations from other pending requests. As a result, a confirmation cast by a member who is later removed still counts toward the `num_confirmations` threshold, letting a request execute with fewer than `K` valid, currently-authorized confirmations.

### Finding Description
The intended custody binding for this contract is: a request can only move funds or change contract state if it has been confirmed by at least `num_confirmations` **current** members — i.e. `confirmations counted == live member confirmations`.

- `add_request` creates a request and an empty confirmation set: [1](#0-0) 
- `confirm` executes the request once `confirmations.len() + 1 >= self.num_confirmations`, without re-validating that each already-stored confirming key is still in `self.members`: [2](#0-1) 
- `delete_member` removes a departing member from `self.members`, and deletes only the requests **they authored** (`r.member == member`). It never inspects `self.confirmations` of other members' pending requests to strip out the removed member's stale confirmation: [3](#0-2) 
- `remove_request`/`assert_valid_request` likewise never re-validate the membership of already-recorded confirmers: [4](#0-3) 

Concrete break of the equality (assume members `{A, B, C}`, `num_confirmations = 2`):
1. `A` calls `add_request` for a `Transfer` action, then `confirm` → `confirmations = {A}` (len 1, not yet ≥ 2).
2. `B` and `C` later execute a separate `DeleteMember{A}` request (via their own 2-of-3 confirmation), removing `A` from `self.members`. `delete_member` does not touch the confirmation set of `A`'s pending transfer request, since that request wasn't authored by `A`... wait, it was authored by `A`, so it *would* be removed in this exact scenario.

To make the analog concrete, the authored-request filter must be evaded: `A` confirms (but does not create) a request created by `B`.
1. `B` calls `add_request` for `Transfer{amount}` to an attacker-controlled account.
2. `A` calls `confirm(request_id)` → `confirmations = {A}` (len 1, `1+1 < 2`, not executed).
3. Members vote to remove `A` (e.g., because `A`'s key was compromised) via `DeleteMember{A}`. `delete_member` deletes only requests where `r.member == A` (requests *A authored*) — it does **not** touch the confirmation set of `B`'s request, which still lists `A` as a confirmer, and `A` is no longer in `self.members`.
4. `C` calls `confirm(request_id)` → `confirmations.len() == 1` (`{A}`) `+ 1 (C) = 2 >= num_confirmations (2)` → `execute_request` runs and transfers NEAR to the receiver, even though only one currently-valid member (`C`) actually approved it.

This breaks the "confirmations counted versus live members" custody binding: the contract records 2 confirmations, but only 1 belongs to a live, currently-authorized member.

### Impact Explanation
This matches the Critical impact category: "a multisig request executed below threshold." An attacker who compromises or is a formerly-legitimate member's key can pre-confirm arbitrary `Transfer`, `FunctionCall`, `AddKey`, or `DeployContract` actions before being removed; once removed, their stale confirmation is never invalidated, letting the remaining members (or a single additional colluding/compromised member) push the request through with fewer genuinely authorized confirmations than the configured `K`. Given the contract governs NEAR balances and access-key management for the account it controls, this can move NEAR or deploy/execute privileged actions without the intended quorum.

### Likelihood Explanation
This requires a legitimate member-removal event (a normal, expected multisig operation — e.g. rotating a compromised or departing member) combined with a pending request that the removed member had previously confirmed but did not author. Member removal and rotation are exactly the operations a multisig is expected to support, so the precondition is realistic rather than exotic; no redeploy, foundation privilege, or social engineering is needed beyond the multisig's own documented member-management flow.

### Recommendation
When a member is deleted, iterate over all pending requests' confirmation sets (not just requests authored by that member) and remove any confirmation entry belonging to the removed member, or alternatively re-validate at `confirm`-time that every entry in the stored `confirmations` set is still present in `self.members` before counting it toward the threshold.

### Proof of Concept
Pseudocode against `multisig2/src/lib.rs`:
```
let mut c = MultiSigContract::new(vec![A, B, C], 2);
// B creates a transfer request
set_signer(B); let rid = c.add_request(transfer_request);
// A confirms it (1 confirmation, threshold not met)
set_signer(A); c.confirm(rid);
// Members vote out A (compromised key) via a separate DeleteMember request
set_signer(B); let del = c.add_request(delete_member_request(A));
c.confirm(del);
set_signer(C); c.confirm(del); // executes, A removed from self.members
// c.confirmations.get(&rid) still contains A's stale confirmation
set_signer(C); c.confirm(rid); // confirmations {A,C} len 2 >= 2 -> executes Transfer
// Transfer executes with only 1 live-member confirmation (C), not 2
``` [2](#0-1) [3](#0-2)

### Citations

**File:** multisig2/src/lib.rs (L170-200)
```rust
    pub fn add_request(&mut self, request: MultiSigRequest) -> RequestId {
        let current_member = self.current_member().unwrap_or_else(|| {
            env::panic_str(
                "Predecessor must be a member or transaction signed with key of given account",
            )
        });
        // track how many requests this key has made
        let num_requests = self
            .num_requests_pk
            .get(&current_member.to_string())
            .unwrap_or(0)
            + 1;
        assert(
            num_requests <= self.active_requests_limit,
            "Account has too many active requests. Confirm or delete some.",
        );
        self.num_requests_pk
            .insert(&current_member.to_string(), &num_requests);
        // add the request
        let request_added = MultiSigRequestWithSigner {
            member: current_member,
            added_timestamp: env::block_timestamp(),
            request,
        };
        self.requests.insert(&self.request_nonce, &request_added);
        let confirmations = HashSet::new();
        self.confirmations
            .insert(&self.request_nonce, &confirmations);
        self.request_nonce += 1;
        self.request_nonce - 1
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

**File:** multisig2/src/lib.rs (L381-423)
```rust
    /// Removes request, removes confirmations and reduces num_requests_pk - used in delete, delete_key, and confirm
    fn remove_request(&mut self, request_id: RequestId) -> MultiSigRequest {
        // remove confirmations for this request
        self.confirmations.remove(&request_id);
        // remove the original request
        let request_with_signer = self
            .requests
            .remove(&request_id)
            .unwrap_or_else(|| env::panic_str("Failed to remove existing element"));
        // decrement num_requests for original request signer
        let original_member = request_with_signer.member;
        let mut num_requests = self
            .num_requests_pk
            .get(&original_member.to_string())
            .unwrap_or(0);
        // safety check for underrun (unlikely since original_signer_pk must have num_requests_pk > 0)
        if num_requests > 0 {
            num_requests = num_requests - 1;
        }
        self.num_requests_pk
            .insert(&original_member.to_string(), &num_requests);
        // return request
        request_with_signer.request
    }

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

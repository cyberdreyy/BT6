## Analysis: Stale confirmations from deleted multisig members are still counted toward threshold

This is a solid analog to the Story Protocol bug: just as `tagDerivativeIfParentInfringed` used a stored `arbitrationPolicy` value without refreshing it against current state, the `multisig2` contract's `confirm` function counts confirmations recorded in `self.confirmations` without verifying that the confirming members are still active members. `delete_member` only purges requests and confirmations *created by* the deleted member, not confirmations *given by* that member on requests created by someone else, leaving stale confirmations that still count toward `num_confirmations`.

### Title
Stale confirmations from removed multisig members still count toward execution threshold - (`multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` in `multisig2/src/lib.rs` decides whether to execute a request purely by comparing the size of the stored `confirmations` set (plus one) against `self.num_confirmations`. `delete_member` (invoked through `DeleteMember` action execution) removes the deleted member from `self.members` and cleans up requests/confirmations for requests the deleted member itself created, but it never scans `self.confirmations` of *other* outstanding requests to strip out that member's prior confirmation. As a result, a confirmation cast by an account that is no longer a multisig member can still be counted when totaling confirmations for another pending request, allowing that request to reach the threshold and execute with fewer live, currently-authorized confirmations than `num_confirmations` requires.

### Finding Description
`confirm` computes the quorum check as: [1](#0-0) 

The equality that should hold is: `count(confirmations that are from members ∈ self.members) >= num_confirmations`. Instead, the code checks `count(confirmations recorded historically) >= num_confirmations`, without re-verifying membership of every entry in the confirmations set.

`delete_member` only cleans confirmations/requests where the deleted member was the *request creator* (`r.member == member`), and only removes that member's own `num_requests_pk` entry — it does not touch `self.confirmations` entries on requests created by other members where the deleted member had previously called `confirm`: [2](#0-1) 

Compare with `remove_request`, which only fires when a request is fully executed or deleted, not when a member is removed: [3](#0-2) 

So once member B confirms request R1 (created by member A), and B is later removed from `self.members` via a `DeleteMember` request that executes successfully, R1's `confirmations` set in storage still contains B's confirmation string. When A (or any other live member) subsequently calls `confirm(R1)`, the code computes `confirmations.len() as u32 + 1 >= self.num_confirmations`, which includes B's now-invalid confirmation, potentially reaching quorum with one fewer *live* confirming member than `num_confirmations` mandates.

### Impact Explanation
This breaks the “confirmations counted versus live members” binding called out in the rules. A multisig configured for e.g. 2-of-3 could execute a `Transfer`, `AddKey`, `FunctionCall`, or any other `MultiSigRequestAction` with only 1 genuinely live confirmation plus 1 phantom confirmation from a removed member — i.e., a request executed below the intended threshold. Since `MultiSigRequestAction::Transfer` moves NEAR out of the account and `AddKey`/`FunctionCall` can grant further control, this is a Critical-class impact: “a multisig request executed below threshold” with concrete fund-moving/authorization consequences.

### Likelihood Explanation
This requires no privileged position beyond being one of the remaining legitimate members (which is the normal operating mode of a multisig) and a pre-existing confirmation from a member who is later removed — a highly plausible operational sequence for any multisig that rotates members (e.g. team member offboarding, key rotation, compromised-key removal). No malicious validator, redeploy, or foundation/owner privilege is needed beyond ordinary multisig membership actions already supported by the contract's own API.

### Recommendation
When executing `DeleteMember`, iterate over all outstanding requests in `self.requests` and strip the deleted member's entry from every corresponding `self.confirmations` set (not just requests the deleted member created). Alternatively, change `confirm` to recompute the quorum by filtering `confirmations` against `self.members` at confirmation time, i.e. `confirmations.iter().filter(|m| self.members.contains(m)).count() + 1 >= self.num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, C]`, `num_confirmations = 2`.
2. A calls `add_request` to create request `R1` (e.g., `Transfer` to attacker-controlled account).
3. B calls `confirm(R1)` — `confirmations = {B}`, count is 1, below threshold of 2, request stays pending.
4. Separately, a `DeleteMember { member: B }` request is created and confirmed by A and C (2-of-3), which executes `delete_member` for B: `self.members` becomes `{A, C}`, but `R1`'s `self.confirmations` entry (`{B}`) is left untouched because `R1.member == A`, not `B`.
5. A calls `confirm(R1)` again. `confirmations.len() as u32 + 1 == 2 >= self.num_confirmations (2)` — quorum is reached and `execute_request` fires the `Transfer`, even though only A is a genuinely live confirming member; B's confirmation is a ghost from a removed key/account.

Note: this analysis is based on the code available via the index; a Devin session with full repository access would be needed to additionally check `multisig/src/lib.rs` (the older, `delete_key`-based multisig) for an equivalent gap and to write/execute an integration test confirming the exact runtime behavior.

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

**File:** multisig2/src/lib.rs (L381-404)
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
```

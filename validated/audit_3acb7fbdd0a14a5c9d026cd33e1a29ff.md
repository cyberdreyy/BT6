Confirmed: `assert_valid_request` only checks that the caller (new confirmer) is currently a member — it never re-validates the *existing* confirmers stored in the `confirmations` set. Combined with `delete_member` only purging requests that the removed member themselves *authored* (not ones they merely confirmed), this creates the exact analog of the M-16 bug class.

### Title
Stale confirmations from removed multisig members are not purged, allowing requests to execute below the live-member threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` counts confirmations recorded in the `confirmations: LookupMap<RequestId, HashSet<String>>` map against `num_confirmations` without verifying that every recorded confirmer is still a current member. `delete_member` only removes pending requests that the *removed* member originally authored, but never cleans up confirmations that removed member left on *other* still-pending requests. As a result, a confirmation from an already-removed member remains permanently counted toward the approval threshold of any request it was cast on before removal.

### Finding Description
`confirm()` performs the threshold check purely on the size of the stored `HashSet<String>` of confirmer identities: [1](#0-0) 

The only membership check performed is on the *new* caller via `assert_valid_request` → `current_member()`, which validates the current caller, not the historical confirmers already present in the set: [2](#0-1) 

When a member is removed via `DeleteMember`, `delete_member` only deletes requests where `r.member == member` (i.e., requests *authored* by the removed member) and removes that member's `num_requests_pk` entry and access key — it does not scan `confirmations` for entries referencing the removed member on requests authored by someone else: [3](#0-2) 

This breaks the intended invariant `confirmations counted == confirmations by live members`. Once a member is removed, any confirmation they cast earlier on a still-pending request continues to count as a valid vote forever, effectively reducing the real number of live approvers needed to reach `num_confirmations`.

### Impact Explanation
This allows a multisig request to be executed with fewer genuinely authorized (currently trusted) confirmations than `num_confirmations` requires — i.e., a request is executed below the live-member threshold. If the removed member was malicious or compromised, their "phantom" prior confirmation on a pending malicious request (e.g., a `Transfer`, `AddKey`, or arbitrary `FunctionCall`) lets the remaining, possibly minority, of live members push it through with fewer live signers than intended, moving funds or granting access with a request executed below the configured threshold.

### Likelihood Explanation
No special privileges beyond being one current multisig member are needed to trigger the loss of the invariant; a previously-added member (later removed for being malicious or compromised) merely needs to have confirmed a pending request before removal. This is easily reachable through the contract's normal `add_request` / `confirm` / `DeleteMember` flow with no need to bypass access control, and the flaw persists as long as any confirmation predates a later membership change.

### Proof of Concept
1. Multisig initialized with members `{A, B, C}` and `num_confirmations = 2`.
2. `A` calls `add_request` with a malicious `MultiSigRequestAction::Transfer` (request id `R`), no auto-confirm.
3. `C` (soon to be identified as malicious/compromised) calls `confirm(R)` → `confirmations[R] = {C}` (1 of 2, not yet executable) — see `confirm` logic at [4](#0-3) .
4. The remaining honest members `A` and `B` submit and confirm a `DeleteMember { member: C }` request, which executes via `delete_member`, removing `C` from `members` and revoking their key — but `R` was authored by `A`, not `C`, so it is not among the `request_ids` purged, and `confirmations[R]` still equals `{C}`: [5](#0-4) 
5. `A` (the original requester, who never confirmed) now calls `confirm(R)`. `assert_valid_request` passes because `A` is still a live member. `confirmations.len() as u32 + 1 = 1 + 1 = 2 >= num_confirmations(2)`, so `execute_request` fires the malicious `Transfer`.
6. Only one currently live member (`A`) actually approved `R` at execution time — the second "vote" came from `C`, who had already been removed from the multisig — yet the contract treated the request as fully 2-of-3 approved.

### Recommendation
When executing `delete_member`, also purge the removed member's identity from every entry in `confirmations` (not just requests they authored), or alternatively, when checking the threshold in `confirm`, re-validate that every address/key in the stored confirmation set is still contained in `self.members` before counting it toward `num_confirmations`.

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

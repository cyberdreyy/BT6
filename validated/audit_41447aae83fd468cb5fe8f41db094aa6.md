Confirmed root cause: `delete_member()` in `multisig2/src/lib.rs` only purges requests/confirmations that were *originated* by the removed member (`r.member == member`), but does not scrub the removed member's confirmation entries that they may have cast on requests originated by *other* members. Since `confirmations` is a `HashSet<String>` keyed by `member.to_string()`, a stale confirmation from a now-deleted member remains counted toward the `num_confirmations` threshold in `confirm()`.

### Title
Stale confirmations from deleted multisig members can be counted toward execution threshold, allowing sub-threshold execution - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::delete_member()` removes a member from `self.members` and deletes only the requests the removed member *created*, but leaves intact any confirmation entries that member previously added to requests created by *other* members. `confirm()` counts confirmations purely by cardinality of the stored `HashSet<String>` against the current `self.num_confirmations`, without verifying that every recorded confirmer is still a live member. This breaks the intended equality: `confirmations counted == confirmations from currently authorized members`.

### Finding Description [1](#0-0) 

`confirm()` computes `confirmations.len() as u32 + 1 >= self.num_confirmations` and, once satisfied, calls `execute_request()`. This purely counts set membership size, never re-validating that each entry in `confirmations` corresponds to a `MultisigMember` still present in `self.members`. [2](#0-1) 

`delete_member()` filters `self.requests` for `r.member == member` (the request's *creator*) and purges only those requests' confirmations. It does not scan the `confirmations` map for entries where the deleted member's serialized identity appears as a *confirmer* on requests they didn't create. Those stale confirmation strings persist in the `HashSet<String>` for any pending request the deleted member had previously confirmed.

The binding that should hold: `live confirmations on a request ⊆ current members`. After `delete_member`, this invariant can be violated — a pending request can carry a confirmation from an account/key no longer part of the multisig, and that confirmation still counts toward the K-of-N threshold.

### Impact Explanation
If a request is created and partially confirmed (below threshold) by several members, and one of the confirming (non-creating) members is later removed via `DeleteMember`, their confirmation is not purged. When the remaining members subsequently confirm, the threshold check can be satisfied using count that includes the stale, no-longer-authorized confirmation — effectively executing a request (e.g., a `Transfer`, `AddKey`/`AddMember`, or `FunctionCall`) with fewer *currently authorized* confirmations than `num_confirmations` mandates. This is a multisig request executed below the intended threshold, moving NEAR (or granting access) without full authorization from the live member set — a Critical-class custody/authorization violation per the impact definitions (funds moved / authorization threshold not actually met).

### Likelihood Explanation
Requires an internal multisig action (`DeleteMember`) to occur while a pending request from another member already carries a confirmation from the member being removed — a realistic operational sequence (e.g., offboarding a compromised or departing member) rather than a contrived edge case. No external governance change or privileged attacker capability beyond normal multisig operation is needed; it can happen from ordinary membership churn.

### Recommendation
When deleting a member in `delete_member()`, additionally iterate over all pending requests' `confirmations` sets (not just those the member created) and remove any entry equal to `member.to_string()`. Alternatively, re-validate at `confirm()` time that every string in the stored confirmation set still corresponds to a current member before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy multisig2 with members `[A, B, C, D]`, `num_confirmations = 3`.
2. Member `B` calls `add_request` for a `Transfer` (request creator = B).
3. Member `C` calls `confirm(request_id)` → confirmations = `{C}` (1/3), pending.
4. Multisig executes a separate request to `DeleteMember { member: C }` (reaching threshold via other members) — `delete_member` only purges requests where `r.member == C` (none, since C didn't create the transfer request); C's confirmation entry on the transfer request is left untouched.
5. Now only `{A, D}` remain as valid members besides B (creator). `D` calls `confirm(request_id)` → confirmations = `{C, D}`, size 2, `+1 (implicit for current confirmer or creator depending on flow)` reaches `3 >= num_confirmations`, and `execute_request` fires — using C's stale confirmation despite C no longer being a member, i.e., only 2 *live* members (B as creator-context and D) effectively authorized a 3-of-N action.

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

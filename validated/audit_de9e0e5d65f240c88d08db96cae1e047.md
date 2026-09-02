This confirms the vulnerability path in `multisig2/src/lib.rs`. The `delete_member` function at lines 356-379 only purges confirmations/requests for requests **authored** by the removed member (`filter_map(|(k, r)| if r.member == member ...)`), but never scans the `confirmations` map to strip that member's confirmation entries from *other* pending requests where they had already called `confirm()` but did not author. The `confirm()` function at lines 292-315 later counts `confirmations.len()` toward `self.num_confirmations` without re-validating that every stored confirming member is still `self.members.contains(&member)`.

### Title
Stale Confirmations From Removed Multisig Members Allow Execution Below Live Threshold - (File: multisig2/src/lib.rs)

### Summary
`delete_member` in the multisig2 contract removes a member from `self.members` and deletes only the requests *authored* by that member, but does not purge that member's confirmation entries recorded in the `confirmations: LookupMap<RequestId, HashSet<String>>` map for requests authored by *other* members. A confirmation cast by a member before their removal continues to count toward `num_confirmations` on any pre-existing pending request, even after that member is deleted.

### Finding Description
The custody binding that should hold is: **confirmations counted toward `num_confirmations` == confirmations from currently-live members**. `confirm()` blindly trusts the size of the stored `HashSet<String>` in `self.confirmations`: [1](#0-0) 
It never re-checks that every string in that set is still present in `self.members`.

`delete_member` only cleans up requests where the removed member is the *original requester* (`r.member == member`): [2](#0-1) 
It does not iterate `self.confirmations` to strip the removed member's confirmation from requests authored by someone else. This is directly analogous to the reported TSS bug class: a trust anchor (the member/key set) is rotated, but state that grants authority under the old anchor (a previously recorded confirmation) is not invalidated, so it can still be "replayed" to help satisfy the new threshold.

### Impact Explanation
Example with 5 members, `num_confirmations = 3`:
1. Member A creates a request via `add_request_and_confirm` (adds A's confirmation) — confirmations = `{A}`.
2. Member B calls `confirm` — confirmations = `{A, B}` (2/3, not yet executed).
3. Members subsequently pass a separate `DeleteMember { member: B }` request, removing B from `self.members`. The original A-authored request is untouched (`r.member == A != B`), so its confirmations set `{A, B}` (still containing B) is never cleared.
4. Member C now calls `confirm` on the original request — confirmations become `{A, B, C}`, `len() == 3 >= num_confirmations`, and the request executes (e.g. a `Transfer` or `AddKey`), even though B is no longer an authorized member.

This lets a request execute with fewer than `num_confirmations` *currently authorized* signers, effectively lowering the live approval threshold — falling under "a multisig request executed below threshold," a Critical-impact custody-binding violation, potentially moving NEAR or granting access keys without sufficient current authorization.

### Likelihood Explanation
This requires no attacker privilege beyond being a normal member who confirms requests before departure, or simply the natural operational flow of removing/rotating a compromised or departing member while an unrelated request is pending — a realistic and common multisig maintenance scenario, not a contrived edge case.

### Recommendation
When executing `DeleteMember`, iterate all entries in `self.confirmations` (not just requests authored by the removed member) and remove the deleted member's key from every confirmation set; alternatively, re-validate each stored confirming member against `self.members` inside `confirm()` before counting them toward the threshold.

### Proof of Concept
1. Deploy `multisig2` with 5 members `[A, B, C, D, E]`, `num_confirmations = 3`.
2. As A: `add_request_and_confirm(transfer_request)` → confirmations = `{A}`.
3. As B: `confirm(request_id)` → confirmations = `{A, B}` (2/3).
4. As D and E (self-request against the multisig account): `add_request_and_confirm` + `confirm` a `DeleteMember { member: B }` request with 3 confirmations → B removed from `self.members`; `transfer_request`'s confirmation set is untouched because it was authored by A, not B (see `delete_member`, `multisig2/src/lib.rs:361-371`).
5. As C: `confirm(transfer_request_id)` → confirmations `{A, B, C}` = 3, `>= num_confirmations`, request executes and funds transfer out — despite B no longer being a valid member. [2](#0-1) [1](#0-0)

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

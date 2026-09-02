### Title
Stale confirmations from removed multisig members are still counted toward the confirmation threshold, allowing a request to execute below the real live-member quorum - ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` only purges pending requests that were *authored* by the removed member; it never scrubs that member's `confirm()` votes from the `confirmations` set of other, still-pending requests. Since `confirm()` only compares `confirmations.len()` to `self.num_confirmations` (never re-validating that each entry in the set still corresponds to a current member), a confirmation cast by an account/key that is later removed from `members` keeps counting toward the threshold forever. This lets a request execute with fewer *live* member approvals than `num_confirmations` requires.

### Finding Description
- `confirm()` (`multisig2/src/lib.rs:294-315`) checks membership of the *new* voter via `assert_valid_request` → `current_member()`, but the historical entries already stored in `self.confirmations.get(&request_id)` are opaque strings that are never re-checked against `self.members`. [1](#0-0) 
- `delete_member()` (`multisig2/src/lib.rs:356-379`) removes the departing member from `self.members`, and cleans up only the requests it filters with `r.member == member` — i.e., requests that member *originated*. It never scans other requests' `confirmations` sets to strip that member's vote. [2](#0-1) 
- The binding that should hold is: `confirmations counted == confirmations from accounts currently in self.members`. After a `DeleteMember` action, this equality breaks for any request that the removed member had already confirmed but that is still pending — the stale entry is retained in the `HashSet<String>` and is indistinguishable from a valid current-member confirmation.

### Impact Explanation
This falls under the Critical bucket "a multisig request executed below threshold." A `Transfer`, `AddKey`/`DeleteKey`, `FunctionCall`, or `DeployContract` request can be pushed through `execute_request` with real live-member approval strictly less than `num_confirmations`, effectively lowering the quorum without any of the current members explicitly agreeing to that. Since multisig accounts commonly custody the account's NEAR balance and access keys, this can result in unauthorized transfers or unauthorized key/contract changes being approved with an insufficient number of genuinely-current signers.

### Likelihood Explanation
This requires normal, expected multisig lifecycle activity, not a "malicious validator," "redeploy," or "social engineering": member rotation (removing a departed employee, replacing a lost/rotated key, revoking a compromised key) is a routine multisig maintenance operation exposed as `DeleteMember`. Any member (including a colluding/malicious one) can leave one or more of their own or a soon-to-be-removed member's confirmations "banked" on a pending request simply by creating it and getting it confirmed before the `DeleteMember` request executes. No special privilege beyond being a normal multisig member is required to set up and benefit from the stale vote.

### Recommendation
When executing `DeleteMember`, iterate over **all** pending requests (not only ones the member authored) and remove the departing member's entry from each request's `confirmations` set. Alternatively, validate at `confirm()`/`execute_request()` time that every entry in `confirmations` still corresponds to a member in `self.members`, discarding stale entries (and/or recomputing `confirmations.len()` from only currently-valid members) before comparing against `num_confirmations`.

### Proof of Concept
1. Initialize `MultiSigContract::new(members = [A, B, C], num_confirmations = 3)`.
2. Member `A` calls `add_request` for `Transfer { amount }` to itself → `request_id = R`.
3. Member `B` calls `confirm(R)` → `confirmations[R] = {A, B}` (2/3).
4. Separately, a legitimate `DeleteMember { member: B }` request is created and confirmed by the required quorum (e.g., because `B`'s key was rotated/compromised) and executes via `delete_member` (`multisig2/src/lib.rs:356-379`). This only removes requests where `r.member == B` (i.e., requests `B` created); request `R` (created by `A`) is untouched, and `confirmations[R]` still contains `B`.
5. `self.members` is now `{A, C}`, so a legitimate quorum should require confirmations from 3 of `{A, C}` — impossible since only 2 members remain, effectively `num_confirmations` should be lowered by governance, but it hasn't been.
6. Member `C` calls `confirm(R)`. In `confirm()` (`multisig2/src/lib.rs:304`), `confirmations.len() as u32 + 1 == 3 >= num_confirmations (3)`, so `execute_request` fires the transfer — even though only `A` and `C` are current members who actually approved it; `B`'s stale vote (from a member removed from the multisig) supplied the third, unauthorized confirmation.

### Citations

**File:** multisig2/src/lib.rs (L294-315)
```rust
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

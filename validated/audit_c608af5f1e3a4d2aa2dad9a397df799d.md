### Title
Stale confirmations from removed multisig members are still counted toward the approval threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` only purges requests that were *authored* by the removed member; it never scans the `confirmations` map to strip that member's confirmation from other requests they had merely co-signed. Because `confirm()` counts entries in that stale set toward `num_confirmations`, a request can later execute with fewer *live* member approvals than the configured threshold.

### Finding Description
`confirm()` reads the confirmation set for a request and executes once `confirmations.len() + 1 >= self.num_confirmations`, without ever re-validating that the accounts/keys already in the set are still members: [1](#0-0) 

`delete_member`, invoked from `execute_request` when a `DeleteMember` action reaches threshold, only removes requests where the deleted member is the *originator* (`r.member == member`) and cleans up `num_requests_pk`; it does not touch `self.confirmations` for requests originated by someone else that the removed member had confirmed: [2](#0-1) 

`current_member()` is only used to gate who is allowed to call `confirm`/`add_request`; once a confirmation string is already stored in `self.confirmations`, membership is never re-checked for it: [3](#0-2) 

This breaks the binding: `confirmations.len()` (recorded approvals) should equal the number of *currently live* members who approved, but after a `DeleteMember` execution it can retain approvals from accounts that are no longer members, i.e. `confirmations.len() > |{live approvers}|`.

### Impact Explanation
This lets a request execute with fewer live-member confirmations than `num_confirmations` requires — a multisig request executed below its intended threshold, which is explicitly a Critical-severity outcome (an authorization/threshold boundary is broken, potentially allowing unauthorized transfers, key additions, or contract calls with less real consensus than configured).

### Likelihood Explanation
This occurs through entirely normal, in-scope multisig operation (no malicious member, no owner, no external contract required): any time a member confirms a request they did not create, and that member is later removed via a standard `DeleteMember` action while the earlier request is still pending, the stale confirmation persists. Given the `ACTIVE_REQUESTS_LIMIT`/cooldown design implies multiple concurrent pending requests are expected, this is a realistic sequence rather than a contrived edge case.

### Recommendation
When executing `DeleteMember`, iterate all entries of `self.confirmations` (not just requests authored by the removed member) and remove the deleted member's string from every confirmation set, or alternatively re-validate that all entries in a confirmation set still belong to `self.members` before counting them in `confirm()`.

### Proof of Concept
1. Deploy `MultiSigContract::new` with members `{A, B, C, D, E}` and `num_confirmations = 3`.
2. `C` calls `add_request(R)` for a `Transfer` action (request `R` created by `C`, no confirmations yet).
3. `X` (member `D`) calls `confirm(R)` → `confirmations[R] = {D}` (1 confirmation).
4. Separately, members execute a `DeleteMember { member: D }` request (reaching its own 3-of-5 threshold normally) — see `execute_request` dispatch and `delete_member`: [4](#0-3) 
   This removes `D` from `self.members`, but `confirmations[R]` still contains `D`'s entry because `delete_member`'s cleanup only filters `requests` where `r.member == D` (i.e., requests *created* by D), not confirmation sets where D merely voted: [5](#0-4) 
5. Now only `A` and `B` (two live members) confirm `R`: `confirmations[R]` grows to `{D, A, B}` = 3 entries, and `confirm()` executes the request since `3 >= num_confirmations`, even though only 2 currently-live members (plus the original creator not even counted) actually approved it. [6](#0-5)

### Citations

**File:** multisig2/src/lib.rs (L239-242)
```rust
                MultiSigRequestAction::DeleteMember { member } => {
                    self.assert_self_request(receiver_id.clone());
                    self.delete_member(promise, member)
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

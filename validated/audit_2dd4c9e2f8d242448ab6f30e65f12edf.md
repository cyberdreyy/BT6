### Title
Stale confirmations from removed multisig members are not purged from pending requests, allowing execution below the live-member threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member` only deletes *requests created by* the removed member, but never scrubs that member's *confirmations* recorded on other pending requests. `confirm()` counts confirmations purely by set size (`confirmations.len() as u32 + 1 >= self.num_confirmations`) without checking that every recorded confirmer is still a current member. This breaks the equality the contract is supposed to guarantee: `live confirmations counted == live confirmations required`. Instead, a confirmation cast by an account that has since been removed can still be tallied toward the threshold, letting a request execute with fewer currently-trusted signers than `num_confirmations` mandates.

### Finding Description
`confirm()` reads the confirmation set for a request and compares its size against `self.num_confirmations`: [1](#0-0) 

The set is only cleaned up in two places: when the request itself is executed/removed (`remove_request`, which clears `confirmations` for that one request id), and when a member is deleted: [2](#0-1) 

`delete_member` filters `self.requests` for entries whose `r.member == member` (i.e. requests the removed member *created*) and deletes those requests/confirmations. It never iterates `self.confirmations` to strip the removed member's entry from requests *they merely confirmed but did not create*. Those stale string entries (`MultisigMember::to_string()`) remain in the `HashSet<String>` for any still-pending request.

Because `confirm()`'s threshold check is a pure cardinality check on that set, a stale confirmation from a now-removed member is indistinguishable from a live one and still counts toward `num_confirmations`. This is structurally the same root cause as the Nouns DAO bug: a check (`minimumRewardPeriod` / here, `num_confirmations`) is satisfied using stale/anchor data (an ineligible proposal / here, a since-revoked confirmer) that was never re-validated for eligibility at settlement time.

### Impact Explanation
This crosses the identity/authorization boundary the multisig is meant to enforce: `confirmations from current members == threshold required`. A request can be executed (including `Transfer`, `FunctionCall`, `AddKey`, or further `DeleteMember`/`AddMember` actions) with confirmations that include one or more parties who are no longer members — e.g., a removed/compromised member whose earlier confirmation is "recycled" to help reach quorum after their removal. This matches the Critical impact bucket: "a multisig request executed below threshold."

### Likelihood Explanation
No special privilege is required beyond being (or having been) a legitimate member at some point — which is the normal operating mode of a multisig. The scenario only requires: (1) a pending request exists with a partial confirmation from member X, (2) member X is later removed via a separate, correctly-confirmed `DeleteMember` request, and (3) the original pending request is later confirmed by enough of the remaining members to reach `num_confirmations` counting X's stale entry. This is a plausible, low-effort sequence of ordinary multisig operations (open requests + a membership change) rather than a contrived edge case.

### Recommendation
When deleting a member in `delete_member`, also iterate `self.confirmations` for all pending requests and remove the deleted member's entry from every confirmation set (not just requests they created). Alternatively, make `confirm()` validate that every entry in the confirmation set still corresponds to a current member before comparing against `num_confirmations` (i.e., recompute the count of confirmations from members still present in `self.members` at confirmation time), so a threshold can only be met by currently-trusted signers.

### Proof of Concept
1. Initialize `MultiSigContract::new([A, B, C, D], 3)`.
2. `A` calls `add_request(R)` (e.g., `Transfer` to an attacker-controlled `receiver_id`). `confirmations[R] = {}`.
3. `B` calls `confirm(R)` → `confirmations[R] = {B}` (1 < 3, not executed).
4. `C` calls `confirm(R)` → `confirmations[R] = {B, C}` (2 < 3, not executed).
5. Separately, members legitimately confirm a `DeleteMember{C}` request (3-of-4, satisfied by A, B, D) → `delete_member` executes: `self.requests` is scanned for requests created by `C` (none, since `C` didn't create `R`), so `R`'s confirmation set is left untouched: `confirmations[R]` still `= {B, C}` even though `C` is no longer in `self.members`.
6. `D` (a current member) calls `confirm(R)`: `confirmations[R].len() == 2`, so `2 + 1 >= 3` → the request executes, using `C`'s stale, post-removal confirmation as one of the three required approvals, even though only `B` and `D` are currently trusted members who approved. [3](#0-2) [4](#0-3)

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

### Title
Stale confirmations from removed multisig members are still counted toward the confirmation threshold, allowing a request to execute below the required number of live signers - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::confirm()` in `multisig2/src/lib.rs` counts every entry present in the `confirmations` set for a request against `num_confirmations`, but `delete_member()` never purges a removed member's confirmation from requests that member did not personally create. As a result, a request that already received a confirmation from a member who is later removed from the multisig can still be executed once enough *new* confirmations arrive, even though the number of currently live members who actually approved the request is below the configured threshold `K`.

### Finding Description
`confirm()` only checks that the calling member hasn't already confirmed and then compares the size of the stored `confirmations` set (plus the new confirmation) against `self.num_confirmations`: [1](#0-0) 

It never re-validates that the *existing* entries in `confirmations` still correspond to current members of `self.members`.

`delete_member()` is the only place that cleans up requests/confirmations tied to a removed member, but it only removes requests that the member *originated* (`r.member == member`), i.e. requests created via `add_request`/`add_request_and_confirm` by that member. It does nothing to strip that member's confirmation from *other* requests they had merely confirmed: [2](#0-1) 

So the binding that should hold is:
`count(confirmations for request R) == count(live members who approved R)`

After a member who previously confirmed R is deleted (via a separate, unrelated `DeleteMember` request), that equality breaks: the confirmation count remains inflated by one stale, no-longer-valid member, while the count of live approvers is one less. If new confirmations from remaining live members subsequently bring the (stale-inflated) size to `num_confirmations`, `confirm()` executes the request even though fewer than `num_confirmations` *live* members actually approved it.

`assert_valid_request()` also does not perform this check — it only verifies the caller is a current member and that the request/confirmations exist: [3](#0-2) 

### Impact Explanation
This breaks the fundamental K-of-N guarantee documented for the contract ("All operations going forward will require `K` members to call `confirm`"): [4](#0-3) 

A `Transfer`, `FunctionCall`, `DeployContract`, `AddKey`, etc. request can be executed (funds moved, keys added, contracts deployed) with approval from fewer live/current members than the configured threshold. This matches the Critical severity criterion "a multisig request executed below threshold" — funds or privileged actions could be authorized without the intended quorum of currently-trusted members.

### Likelihood Explanation
This requires only normal multisig operation, no foundation/owner/redeploy/social engineering: any pending request that has received partial confirmations, followed by a legitimate (properly-thresholded) `DeleteMember` action removing one of those confirmers, followed by enough remaining members confirming the original request to reach `num_confirmations` in raw set size. A malicious or careless member set can trigger this in the ordinary course of governance (e.g., a departing/compromised member is removed while they had outstanding stale confirmations on other pending requests), making this readily reachable.

### Recommendation
When counting confirmations toward the threshold in `confirm()`, filter `confirmations` to only members currently present in `self.members` (or, equivalently, actively purge a member's confirmation entries from every request's `confirmations` set inside `delete_member()`, not just requests they originated). The threshold check should be based on the count of *live* member approvals, not the raw historical size of the stored set.

### Proof of Concept
1. Initialize `MultiSigContract::new(members = [A, B, C, D], num_confirmations = 3)`.
2. `A.add_request(R)` — creates request `R` (receiver_id = current_account_id, e.g. a `Transfer` action), `confirmations(R) = {}`.
3. `B.confirm(R)` → `confirmations(R) = {B}` (size 1 < 3, stored, not executed).
4. `C.confirm(R)` → `confirmations(R) = {B, C}` (size 2 < 3, stored, not executed).
5. Separately, members legitimately pass and execute a `DeleteMember { member: C }` request (properly reaching 3 confirmations from A, B, D). `delete_member()` runs: it only purges requests where `r.member == C` (i.e., requests C personally created via `add_request`); `R` was created by `A`, so `R`'s stored confirmations `{B, C}` are left untouched. `C` is removed from `self.members` and its access key deleted.
6. `D.confirm(R)` → `confirmations.len() + 1 == 3 >= num_confirmations (3)` → `execute_request(R)` runs.
7. Result: `R` executes with the threshold "met" by `{B, C, D}`, but `C` is no longer a member — only `B` and `D` are actually live members who approved, i.e. only 2 live confirmations authorized a 3-of-N action, breaking the multisig's threshold guarantee.

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

**File:** multisig2/README.md (L12-14)
```markdown
Multisig uses set of `FunctionCall` `AccessKey`s and account ids as a set of allowed N members. 
When contract is being setup, it should be initialized with set of members that will be initially managing this account.
All operations going forward will require `K` members to call `confirm` to be executed.
```

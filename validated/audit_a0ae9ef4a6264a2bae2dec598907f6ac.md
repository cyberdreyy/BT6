### Title
Confirmations from removed multisig members are not purged, allowing a request to execute below the live-member confirmation threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::delete_member()` in `multisig2/src/lib.rs` only removes requests/confirmations that were *created* by the member being removed. It never scans other still-pending requests to strip a confirmation the removed member had already cast on a request created by someone else. Because `confirm()` only counts `confirmations.len()` against `num_confirmations` and never re-validates that every entry in the stored confirmation set still corresponds to a current member, a request can later be executed with fewer live-member approvals than `num_confirmations` requires — the stale confirmation from an already-deleted member still counts toward the threshold.

### Finding Description
The relevant invariant the multisig is supposed to enforce is: `count(confirmations by CURRENT members) >= num_confirmations` before `execute_request()` runs. Instead the code enforces: `count(confirmations recorded historically) >= num_confirmations`, silently assuming that every string stored in `confirmations: LookupMap<RequestId, HashSet<String>>` still corresponds to an active member — this is the same class of unchecked assumption flagged in the external report ("we can assume X is true because Y holds", without actually re-checking X at the point it matters).

- `confirm()` adds the caller's member id to `confirmations` and executes once the set size reaches `num_confirmations`: [1](#0-0) 

- `assert_valid_request()` only validates that the *caller* is currently a member; it does nothing to validate the members already present in the stored `confirmations` set: [2](#0-1) 

- `delete_member()` removes a member and cleans up only the requests where `r.member == member`, i.e. requests *created* by the removed member. It does not iterate over other pending requests to strip confirmations the removed member cast on requests created by other members: [3](#0-2) 

So a confirmation cast by member `X` on request `R` (created by member `Y`) survives in `self.confirmations[R]` even after `X` is deleted via a separate `DeleteMember` request, because `delete_member` only clears requests whose `member` field equals `X` (i.e., requests `X` created), not requests `X` merely confirmed.

### Impact Explanation
This breaks the core custody/authorization binding of the multisig: "confirmations counted" must equal "live members who approved." When it doesn't, a request touching contract funds or contract control (`Transfer`, `AddKey`, `AddMember`, `DeleteMember`, `FunctionCall`, `DeployContract`) can be executed with fewer than `num_confirmations` currently-authorized approvals — i.e. a multisig request executed below threshold, which the rules classify as **Critical**. Concretely, if `num_confirmations = 3` and one of the three confirmations on a pending `Transfer` request came from a member who has since been removed, only 2 live members actually approved the transfer, yet it will still execute and move NEAR out of the multisig account.

### Likelihood Explanation
This requires no privileged access beyond what any legitimate but temporarily-authorized member already has, and no compromise of a foundation/owner key: it is a normal operational sequence — a member confirms a pending request, is later removed as a routine membership change (e.g., key rotation, offboarding), and the previously-confirmed request is left open and later completed by the remaining members. Multisig membership churn (`AddMember`/`DeleteMember`) and having more than one request open at a time are both explicitly supported, ordinary usage patterns of this contract, not edge-case misuse, making the scenario realistic to occur without any attacker even needing to act maliciously.

### Recommendation
In `delete_member()`, when removing a member, iterate over **all** pending requests (not only those the member created) and remove that member's id from each request's `confirmations` set. Alternatively, at the top of `confirm()`/`execute_request()`, re-validate that every entry in the stored confirmation set is still a current member (e.g., `confirmations.retain(|m| self.members.contains(&parse(m)))`) before comparing the count against `num_confirmations`.

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, C, D]`, `num_confirmations = 3`.
2. `A` calls `add_request` to create request `R` (`Transfer` to some receiver), then `add_request_and_confirm`-style `confirm(R)` → confirmations = `{A}`.
3. `B` calls `confirm(R)` → confirmations = `{A, B}` (still below 3, not yet executed).
4. Separately, members submit and confirm a `DeleteMember { member: B }` request and it executes, removing `B` from `self.members`. Per `delete_member` (lines 355-379), only requests created by `B` are purged — `R` (created by `A`) is untouched, so `confirmations[R]` still contains `{A, B}`.
5. `C` (a current, live member) calls `confirm(R)` → confirmations = `{A, B, C}`, size 3 == `num_confirmations`, so `execute_request()` fires and the `Transfer` executes.
6. At execution time only `A` and `C` are actual live members who approved; `B`'s stale confirmation was counted, meaning the transfer executed with 2 live approvals instead of the required 3, moving NEAR below the intended threshold.

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

### Title
Stale confirmations from removed members allow multisig requests to execute below the live confirmation threshold - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::confirm` counts confirmations in `self.confirmations` without verifying that every recorded confirming party is still a current member. `delete_member` only purges confirmations for requests that the removed member *created* (`r.member == member`), not confirmations that member left on other members' requests. A request can therefore execute with `num_confirmations` counted, while the actual number of still-valid, currently-authorized members backing it is lower — the recorded claim (confirmations) diverges from reality (live members), exactly analogous to the ERC4626 `previewRedeem`/`redeem` divergence pattern (recorded value vs. actual value at settlement).

### Finding Description
`confirm()` reads the confirmation `HashSet<String>` for a request and executes as soon as `confirmations.len() as u32 + 1 >= self.num_confirmations`: [1](#0-0) 

Nothing in this path re-checks whether each string in `confirmations` still corresponds to an entry in `self.members`. Membership removal is handled by `delete_member`, which only cleans confirmations/requests where the *removed member is the original requester* (`r.member == member`); it does not scan and purge confirmations that the removed member placed on *other* members' pending requests: [2](#0-1) 

`current_member()` and `assert_valid_request()` only gate who is allowed to *call* `confirm`/`add_request`/`delete_request` at call time — they never revalidate the *stored* confirmations of a request: [3](#0-2) [4](#0-3) 

Binding that is broken: `confirmations.len() (recorded claim) == count of live, currently-authorized members who approved (actual authorization)`. This equality is assumed by `confirm()`'s threshold check but is not enforced once a confirming member is later removed via a *different* request.

### Impact Explanation
This falls squarely under the Critical impact category "a multisig request executed below threshold." An attacker/malicious former member's stale confirmation on a pending request continues to count toward `num_confirmations` even after that member is removed from the multisig. This lets a request (e.g., `Transfer`, `AddKey`, `FunctionCall`) execute with fewer live, currently-trusted approvals than the configured K-of-N policy requires, effectively bypassing the governance threshold the multisig is supposed to enforce and enabling unauthorized fund movement or privilege escalation with fewer real approvers than intended.

### Likelihood Explanation
No special privilege beyond ordinary multisig membership is needed to set up the exploit, and the flawed condition is entirely due to the contract's own bookkeeping (independent per-request confirmation cleanup only for the removed member's own requests). Any lifecycle where a member is removed (e.g., due to key compromise, personnel change, or a compromised member) while they have outstanding confirmations on requests created by others will silently leave those confirmations valid toward the threshold — a realistic and even expected operational sequence for a long-lived multisig (member churn), not a contrived edge case.

### Recommendation
When a member is deleted via `delete_member`, iterate over **all** pending requests (not just those where `r.member == member`) and remove the deleted member's entry from each request's `confirmations` set (or invalidate/reset confirmations entirely on any membership change). Alternatively, in `confirm()`, recompute the count by intersecting stored confirmations with the current `self.members` set before comparing against `num_confirmations`, ensuring only live members' confirmations count toward execution.

### Proof of Concept
1. Deploy `MultiSigContract::new` with `members = {A, B, C, D}` and `num_confirmations = 3`.
2. Member `A` calls `add_request` to create request `R` (e.g., `Transfer` to an attacker-controlled account), then `confirm(R)` — `confirmations[R] = {A}`.
3. Member `B` calls `confirm(R)` — `confirmations[R] = {A, B}` (len=2, still < 3, so `R` remains pending) [1](#0-0) .
4. Separately, members `B`, `C`, `D` create and confirm a `DeleteMember { member: A }` request and execute it (3-of-4 threshold met legitimately) because `A`'s key was compromised. `delete_member` removes `A` from `self.members` but does **not** touch `confirmations[R]` because `R`'s creator field (`r.member`) is `A`, so actually in this exact scenario `R` *would* be cleaned since `A` is the requester of `R`.
5. To bypass the cleanup, have `B` (not `A`) be the requester of `R` instead, with `A` only confirming it (as in step 2-3, but swap creator). Now `delete_member` only filters requests where `r.member == A` — since `R.member == B`, `R`'s confirmations are untouched, leaving `confirmations[R] = {A, B}` even though `A` is no longer a member.
6. Now live member `C` calls `confirm(R)`. `confirmations.len() (2) + 1 = 3 >= num_confirmations (3)` → `R` executes [5](#0-4) , even though only `B` and `C` are currently valid members backing it (2 of the current 3 members `{B,C,D}`), not the 3 distinct live approvals the `num_confirmations=3` policy is meant to guarantee. The removed member `A`'s stale approval was still counted toward execution.

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

**File:** multisig2/src/lib.rs (L321-339)
```rust
    /// Returns current member: either predecessor as account or if it's the same as current account - signer.
    fn current_member(&self) -> Option<MultisigMember> {
        let member = if env::current_account_id() == env::predecessor_account_id() {
            MultisigMember::AccessKey {
                public_key: env::signer_account_pk()
                    .try_into()
                    .unwrap_or_else(|_| env::panic_str("Failed to deserialize public key")),
            }
        } else {
            MultisigMember::Account {
                account_id: env::predecessor_account_id(),
            }
        };
        if self.members.contains(&member) {
            Some(member)
        } else {
            None
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

**File:** multisig2/src/lib.rs (L406-420)
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
```

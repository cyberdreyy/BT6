### Title
Stale confirmations from removed multisig members are still counted toward the confirmation threshold, allowing a request to execute below the configured threshold - ([File: multisig2/src/lib.rs])

### Summary
`delete_member` in `multisig2/src/lib.rs` only purges pending requests that the removed member itself *created*; it does not scrub that member's *confirmations* on requests created by other members. Because `confirm()` counts raw entries in the `confirmations` `HashSet<String>` without checking that each entry still corresponds to a live member, a request can be executed with fewer *live* confirming signers than `num_confirmations` requires, as long as one of the counted confirmations belongs to a member removed after confirming but before the request's final confirmation.

### Finding Description
`confirm()` computes `confirmations.len() as u32 + 1 >= self.num_confirmations` and executes the request once that threshold is reached: [1](#0-0) 

`delete_member()` only cleans up requests where `r.member == member` (requests the removed member *authored*) and removes `num_requests_pk` for that member. It never inspects or scrubs the `confirmations` map for requests authored by *other* members that the removed member had already confirmed: [2](#0-1) 

`assert_valid_request` / `current_member()` only validate that the *caller performing the current action* (confirming or deleting) is a current member; they never re-validate the members already present in a request's stored `confirmations` set: [3](#0-2) [4](#0-3) 

Sequence that breaks the threshold invariant `count(confirmations) == count(live confirming members)`:
1. Members `{A, B, C, D}`, `num_confirmations = 3`.
2. `A` creates a `Transfer` request `R` (`add_request`, no auto-confirm).
3. `B` confirms `R` → `confirmations(R) = {B}` (len 1).
4. `C` confirms `R` → `confirmations(R) = {B, C}` (len 2, still below 3, not executed).
5. Separately, `B` is removed via a `DeleteMember` request (a normal governance action with its own, unrelated confirmation quorum). `delete_member` removes `B` from `self.members` but does **not** touch `confirmations(R)`, since `R.member == A`, not `B`.
6. `D` confirms `R` → `confirmations(R) = {B, C, D}`, len 3 `>= num_confirmations(3)`, and `execute_request` runs the `Transfer`.

At execution time only `C` and `D` are live members whose confirmation is valid — `B`'s stale entry from step 3 is what pushed the count over the threshold. The transfer moves funds with only 2 live signers even though the contract's own invariant, encoded via `num_confirmations = 3`, is meant to require 3.

### Impact Explanation
This breaks the exact custody binding this contract exists to enforce: `confirmations counted == live members confirmed`. A `Transfer`, `AddKey` (full-access key), `DeployContract`, or `FunctionCall` request can be pushed to execution with fewer live signers than the configured `num_confirmations`, i.e. "a multisig request executed below threshold" — a Critical-impact scenario per the custody-binding rules, since it allows moving NEAR (or granting full account control) with insufficient live authorization.

### Likelihood Explanation
This requires no privileged/out-of-scope capability beyond the normal, documented operation of the multisig itself: members confirming requests and members being removed over time (e.g., key rotation, offboarding a departing signer) are core, expected lifecycle operations of this contract, not attacker-privileged actions. Any pending request that received a confirmation from a member who is later removed becomes exploitable by whichever remaining members happen to confirm it afterward — there is no special timing exploit needed beyond normal churn in signer sets, which is realistic for any long-lived multisig.

### Recommendation
When `delete_member` removes a member, iterate all requests' `confirmations` sets and remove any entry equal to the removed member's `to_string()` representation (not just requests authored by that member). Alternatively, validate at `confirm()`/execution time that every entry in `confirmations(request_id)` still corresponds to a member in `self.members` before counting it toward `num_confirmations`.

### Proof of Concept
Using the existing test harness in `multisig2/src/lib.rs`:
1. `MultiSigContract::new(members(), 3)` with members `[Account(alice), Account(bob), AccessKey(pk1), AccessKey(pk2)]`.
2. `alice` calls `add_request` for a `Transfer` (no auto-confirm) — the request's `member` field is `alice`.
3. `bob` calls `confirm(request_id)` → `confirmations = {bob}`.
4. `pk1` calls `confirm(request_id)` → `confirmations = {bob, pk1}` (len 2, `< 3`).
5. Via a separate confirmed `DeleteMember{member: bob}` request (reaching its own quorum among the 4 members), `bob` is removed from `self.members`; `delete_member` does not touch `confirmations` of the pending Transfer request because its `member` field is `alice`, not `bob`.
6. `pk2` calls `confirm(request_id)` → `confirmations.len() + 1 == 3 >= num_confirmations`, and `execute_request` runs the `Transfer`, even though `bob` (whose stale confirmation counted) is no longer a member — only 2 live members (`pk1`, `pk2`) actually authorized it. [1](#0-0) [2](#0-1)

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

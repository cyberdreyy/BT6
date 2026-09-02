### Title
Stale confirmations from removed multisig members remain counted toward the approval threshold, allowing a request to execute with fewer live-member confirmations than `num_confirmations` requires - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::delete_member` in `multisig2/src/lib.rs` only purges pending requests and confirmation records for requests that the *departing member originally proposed* (`r.member == member`). It does not scan the `confirmations` map for entries where that member had merely confirmed (but not proposed) other still-open requests. Those stale confirmation entries remain in the `HashSet<String>` stored per `request_id` and continue to be counted by `confirm()` when computing whether the `num_confirmations` threshold has been reached, even though the member who cast that confirmation is no longer part of `self.members`.

### Finding Description
`confirm()` decides whether a request should execute purely based on the size of the `confirmations` set for that `request_id`: [1](#0-0) 

It never re-validates that every account/key already present in `confirmations` is still a current member — it only validates the *caller* via `assert_valid_request` / `current_member()`: [2](#0-1) 

`delete_member` removes the departing member from `self.members` and cleans up requests they had proposed, but does not touch confirmation entries they left behind on requests proposed by *other* members: [3](#0-2) 

This is structurally identical to the SEDA bug class: a count (`confirmations.len()`) is compared against a threshold (`num_confirmations`) without verifying that every unit contributing to that count still represents a currently-authorized/live participant (member). In SEDA, duplicate signatures inflated `votingPower` versus real distinct validators; here, a stale confirmation from a now-removed member inflates `confirmations.len()` versus the number of currently live members who actually approved.

The equality that should hold is:
`confirmations.len()` (units counted toward approval) == number of *currently live* members who affirmatively confirmed.

After a member departs while having an outstanding confirmation on a pending request they did not propose, this equality breaks: the confirmation count includes at least one entry belonging to a non-member.

### Impact Explanation
This is a High/Critical severity issue depending on what the pending request does: because `MultiSigRequestAction` includes `Transfer`, `AddKey`, `AddMember`/`DeleteMember`, `FunctionCall`, and `SetNumConfirmations`, a request that reaches "enough" confirmations by including a stale, no-longer-authorized confirmer will execute — potentially transferring NEAR, granting access keys, or restructuring the member set — despite having fewer genuinely live approvers than the configured `K`-of-`N` threshold. This directly weakens the multisig's authorization guarantee (a request executed below the intended live-member threshold), matching the "multisig request executed below threshold" Critical impact category.

### Likelihood Explanation
This requires only normal, expected multisig operation, no attacker privilege escalation, no redeploy, and no social engineering:
1. Any member proposes request R (not yet executed).
2. A different member confirms R (adds their entry to `confirmations[R]`).
3. Before R accumulates enough confirmations, the confirming member is removed via a legitimate `DeleteMember` action (e.g., because the key was compromised, or normal membership rotation).
4. R still sits in `requests`/`confirmations` untouched by the removal.
5. Remaining members continue confirming R; the stale confirmation from the removed member is silently included in the count, letting R execute with one fewer *live* signer than `num_confirmations` demands.

This is a realistic and likely-to-occur scenario in any long-lived multisig that rotates membership while having open, unconfirmed requests, which the contract's own `active_requests_limit`/`REQUEST_COOLDOWN` design anticipates (implying requests routinely stay open for extended periods).

### Recommendation
When a member is deleted, iterate all requests in `self.requests` and remove that member's entry from `self.confirmations` for every request (not just requests they proposed). Alternatively, when computing whether the threshold is met in `confirm()`, filter `confirmations` to only those entries whose corresponding `MultisigMember` is still contained in `self.members` before comparing against `num_confirmations`.

### Proof of Concept
Conceptual test flow (Rust, using the existing test harness in `multisig2/src/lib.rs`):
1. `MultiSigContract::new(members: [A, B, C, D], num_confirmations: 3)`.
2. As `A`, call `add_request_and_confirm(request)` → `confirmations[R] = {A}`.
3. As `B`, call `confirm(R)` → `confirmations[R] = {A, B}` (2/3, not yet executed).
4. Members submit and confirm a `DeleteMember{member: B}` request through the normal multisig flow, executing successfully, removing `B` from `self.members`. Note `delete_member` only inspects `self.requests` filtered by `r.member == member`; since `B` did not propose `R`, `R`'s confirmation entry `{A, B}` is untouched.
5. As `C` (a current, live member), call `confirm(R)`: `confirmations.len() (2) + 1 >= 3` → true → `execute_request(R)` runs.
6. Result: `R` executes having only two genuinely live confirmers (`A`, `C`) plus a stale confirmation from the now-removed `B`, despite `num_confirmations = 3` and `B` no longer being an authorized member — confirming the executed-below-threshold condition. [1](#0-0) [3](#0-2)

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

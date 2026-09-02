Confirmed: `delete_member` in `multisig2/src/lib.rs` purges outstanding *requests* signed by the removed member [1](#0-0)  but does **not** scan every other pending request's `confirmations` set to strip a confirmation contributed by that now-deleted member. `confirm()` only removes confirmations for the specific request it executes/removes, and only checks membership of the *current* caller, never re-validating the confirmations already recorded on the request [2](#0-1) .

### Title
Stale confirmations from removed multisig members count toward execution threshold - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::confirm` executes a request once `confirmations.len() + 1 >= num_confirmations`, counting every entry stored in the `confirmations: LookupMap<RequestId, HashSet<String>>` map for that request. `delete_member` only cleans up requests *created* by the removed member; it never removes that member's `confirm()` votes recorded on *other* still-pending requests. This lets a request be executed using confirmations from accounts/keys that are no longer members, effectively bypassing the K-of-N live-member threshold.

### Finding Description
The invariant the multisig is supposed to enforce is: `confirmations counted == confirmations from currently-live members`. The code breaks this equality:

1. Member A (one of N members, threshold K) confirms request R (`confirm` inserts `member.to_string()` into `confirmations[R]`) [3](#0-2) .
2. A separate request removes A via `DeleteMember`, calling `delete_member`, which only purges requests whose `MultiSigRequestWithSigner.member == A` (i.e. requests A *created*) and `num_requests_pk` for A; it does not touch `confirmations` of request R because R was not created by A [4](#0-3) .
3. R's confirmation set still contains A's stale vote. Any other live member confirming R afterward is counted alongside A's stale vote in `confirmations.len() as u32 + 1 >= self.num_confirmations`, so R can execute with fewer *live* member confirmations than `num_confirmations` requires [5](#0-4) .

`current_member()` and `assert_valid_request()` only validate the account performing the current `confirm()` call, never re-validating stored confirmations against present-day membership [6](#0-5) [7](#0-6) .

### Impact Explanation
This breaks the threshold-authorization binding: a request (including `Transfer`, `AddKey`/`AddMember`, `FunctionCall`, etc.) can be executed with fewer genuinely-authorized confirmations than the configured `num_confirmations`, i.e. a multisig request executed below threshold. This matches the Critical impact category "a multisig request executed below threshold."

### Likelihood Explanation
Requires normal, expected multisig operation (creating/confirming a request, later removing a member) with no special privilege beyond being an existing member — a routine sequence (pending request + a subsequent, unrelated membership rotation) rather than an attack requiring compromise. Likelihood depends on timing: an attacker (or even accidental operational sequencing) needs a request left pending across a `DeleteMember` execution, which is plausible in real usage since requests can remain open for a while.

### Recommendation
When executing `DeleteMember`, iterate over all pending requests' `confirmations` sets (not just requests created by the removed member) and remove the deleted member's entry from each; if this drops any request below the required confirmation count, leave it pending (do not auto-execute), and re-validate all confirmations against current membership in `confirm()` before counting them toward the threshold (e.g., filter `confirmations` intersected with `self.members` when checking `>= self.num_confirmations`).

### Proof of Concept
1. Deploy `multisig2` with members `[A, B, C]`, `num_confirmations = 2`.
2. `A.add_request_and_confirm(R)` → `confirmations[R] = {A}`.
3. Separately, `B` and `C` confirm a `DeleteMember{A}` request (2-of-3, valid) → executed via `execute_request` → `delete_member` runs; since R was created by A but not yet fully confirmed, is it purged? — Actually per code, `delete_member` purges requests whose `MultiSigRequestWithSigner.member == A` (i.e., requests *added* by A). R was added by A, so in this exact scenario R itself would be purged. To trigger the described gap, have **B** create request R (`B.add_request(R)`), then **A confirms R** (`confirmations[R] = {A}`), then a separate `DeleteMember{A}` request executes (confirmed by B, C) — since R was created by B, not A, `delete_member`'s cleanup loop (`filter_map` on `r.member == member`) does not touch R, leaving `confirmations[R] = {A}` intact even though A is no longer a member.
4. Now members are `[B, C]`. `C.confirm(R)` → `confirmations.len() (1) + 1 >= num_confirmations (2)` → true → `execute_request(R)` runs, even though only one live member (C) confirmed; A's stale vote was needed to reach the threshold. This demonstrates execution below the intended live-member threshold.

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

**File:** multisig2/src/lib.rs (L322-339)
```rust
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

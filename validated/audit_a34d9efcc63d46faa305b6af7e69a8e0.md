## Title
Stale confirmations from removed multisig members can execute a request below the live signer threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` counts confirmations recorded in the `confirmations` map to decide whether a request has reached `num_confirmations` and should execute. `delete_member` only purges confirmations for requests that were *created* by the removed member; it never scans other pending requests' `confirmations` sets to strip an entry contributed by that member. As a result, a member who is later removed can still have their historical confirmation counted toward the execution threshold of a request created by someone else, allowing a request to execute with fewer live, authorized signers than `num_confirmations` requires.

### Finding Description
`confirm()` reads the confirmation set for a request and compares its cardinality (plus the new confirmer) against `self.num_confirmations`: [1](#0-0) 

`delete_member()` is the only place that cleans up `confirmations`, but it filters strictly by requests whose *creator* (`r.member`) equals the member being deleted: [2](#0-1) 

This leaves a gap: if member `M` merely *confirmed* (did not create) some other pending request `R`, and `M` is subsequently deleted via a separate `DeleteMember` request, `R`'s confirmation set still contains `M`'s entry. No code path exists that walks all `confirmations` on member removal to drop stale entries from non-owned requests. `assert_valid_request` in `confirm()`/`delete_request()` only checks that the *current caller* is a member and that the request/confirmations exist — it never re-validates that *previously recorded* confirmations still belong to current members: [3](#0-2) 

Thus the binding that should hold — `confirmations counted == confirmations by currently-live members` — is broken: `confirmations.len()` can include confirmations from accounts/keys no longer in `self.members`.

### Impact Explanation
This directly matches the Critical impact category "a multisig request executed below threshold." An attacker (or any member acting in good faith who is later removed for cause) can pre-confirm a sensitive request (e.g., `Transfer`, `AddKey`/`FunctionCall`) before being removed. After removal reduces the live member set, the remaining members only need to supply `num_confirmations - 1` (or fewer, if multiple stale confirmations exist) *live* confirmations to trigger execution, because the stale confirmation from the removed member is still silently counted. This lets the multisig execute an action (fund transfer, contract deployment, key addition) that never actually received the required number of currently-authorized signers — an unauthorised move of funds/control below the configured security threshold.

### Likelihood Explanation
Requires: (1) an existing pending request confirmed by a member who did not create it, and (2) a later `DeleteMember` request executed for that member. Both are standard, unprivileged multisig operations (no foundation/owner privilege beyond being an existing multisig member, which is exactly the population this contract is designed to defend against internally). Any member set that experiences turnover (a common real-world event: a signer leaving a team, a compromised key being revoked) while requests are in flight is naturally exposed; it does not require any malicious deployment parameters or ignoring of documented initialization.

### Recommendation
When executing `DeleteMember`, iterate over all entries in `self.confirmations` (not only requests created by the removed member) and remove the deleted member's identifier from every confirmation set. Alternatively, validate at `confirm()`-time (or at execution time) that every account/key present in a request's `confirmations` set is still contained in `self.members`, filtering out stale entries before comparing against `num_confirmations`.

### Proof of Concept
1. Multisig initialized with members `{A, B, C}` and `num_confirmations = 2`.
2. `A` calls `add_request` creating request `R` (e.g., `Transfer`). `R.member = A`, `confirmations(R) = {}`.
3. `B` calls `confirm(R)` → `confirmations(R) = {B}` (count 1, below threshold 2), per `confirm()` logic at `multisig2/src/lib.rs:294-315`.
4. Separately, members create/confirm a `DeleteMember{member: B}` request that reaches 2 confirmations and executes `delete_member`, per `multisig2/src/lib.rs:239-241,355-379`. Since `R` was created by `A`, not `B`, the filter `r.member == member` at line 365 does not match `R`, so `confirmations(R)` is left untouched: it still contains `B`.
5. Members are now `{A, C}`. `A` calls `confirm(R)`: `confirmations.len() (1, from stale B) + 1 >= num_confirmations (2)` → request `R` executes, even though only `A` (a single live member) actually authorized it after `B`'s removal — one fewer live confirmation than the configured threshold requires.

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

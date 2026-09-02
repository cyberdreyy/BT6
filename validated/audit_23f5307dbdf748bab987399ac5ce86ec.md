### Title
Multisig requests can execute below the live-member confirmation threshold because deleting a member/key does not invalidate confirmations that member already cast on other pending requests - (File: `multisig2/src/lib.rs`, `multisig/src/lib.rs`)

### Summary
`confirm()` in both multisig contracts counts confirmations recorded in a request's confirmation set and executes the request once the count reaches `num_confirmations`. When a member (or access key) is removed via `DeleteMember`/`DeleteKey`, the contract only purges *requests that member itself submitted*; it does not scrub that member's confirmations from *other* still-pending requests. A confirmation cast by a member who is later removed therefore keeps counting toward the threshold, letting a request execute with fewer currently-authorized approvals than `num_confirmations` requires.

### Finding Description
In `multisig2/src/lib.rs`, `confirm()` simply checks the size of the stored `confirmations` set against `self.num_confirmations`: [1](#0-0) 

`delete_member()` (invoked from `execute_request` on the `DeleteMember` action) removes the member from `self.members`, but the cleanup it performs on `requests`/`confirmations` is scoped only to requests where `r.member == member`, i.e. requests *that member had added* — not requests the member merely confirmed: [2](#0-1) 

Consequently, if member `X` confirms an unrelated pending request `R` (adding their entry to `confirmations[R]`), and is subsequently removed from `self.members` via a separate `DeleteMember` request, `X`'s confirmation entry remains inside `confirmations[R]`. When enough *other* members later confirm `R`, `confirm()` only compares `confirmations.len() + 1 >= self.num_confirmations` — it never re-validates that every entry in the confirmation set still belongs to `self.members`. This lets `R` execute (e.g. a `Transfer` action moving NEAR out of the multisig account) using one fewer live approval than `num_confirmations` mandates, i.e. `live_confirmations(R) < num_confirmations` while `confirm()` treats it as satisfied.

The same structural gap exists in the v1 contract `multisig/src/lib.rs`: `DeleteKey` (analogous to `delete_member`) only removes requests originated by the deleted public key, not confirmations that key gave on other requests, and `confirm()` performs the identical size-only threshold check: [3](#0-2) [4](#0-3) 

This is the direct analog of the reported bug class: a constraint (here, "N confirmations from currently valid signers") enforced at one call path (adding a fresh confirmation from a still-valid member) is not enforced/re-checked at the path that consumes accumulated state (execution triggered once the raw count crosses the threshold), letting stale authorization ("alias" — a removed member's past confirmation) substitute for a live one.

### Impact Explanation
This breaks the core custody binding of a multisig: *"a multisig request executed below threshold"*, explicitly listed as a Critical impact. An attacker/insider scenario: a member whose key is later revoked (e.g., suspected of compromise, or simply rotated out) can pre-confirm a malicious pending `Transfer`/`FunctionCall`/`AddKey` request before removal; after the member is removed by the remaining honest members (who are unaware their action leaves the stale confirmation intact), only `num_confirmations - 1` currently-live members need to approve for the request to execute, moving NEAR or granting access keys with less real authorization than the configured threshold guarantees.

### Likelihood Explanation
Exploitation requires that a member who has confirmed a pending request is later removed from the multisig — a routine and expected event (key rotation, offboarding, revoking a suspected-compromised member). No special privilege beyond normal multisig member participation is needed to plant the stale confirmation beforehand; the removal itself is performed by honest members unaware of the side effect. This makes it a realistic, moderately likely event over a multisig's lifetime rather than a purely theoretical corner case.

### Recommendation
When executing `DeleteMember` (`multisig2`) / `DeleteKey` (`multisig`), iterate over all pending requests' confirmation sets and remove any confirmation entry belonging to the removed member/key (not just requests they authored). Alternatively, `confirm()` should re-validate, at execution time, that every recorded confirming identity is still present in `self.members` (or still holds a valid access key) before treating the threshold as met.

### Proof of Concept
1. Initialize `MultiSigContract::new(members = [A, B, C, D], num_confirmations = 3)`.
2. Member `B` calls `add_request(R)` for a `Transfer` to an attacker-controlled account.
3. Member `C` calls `confirm(R)` → `confirmations[R] = {B, C}` (2 confirmations, count via `add_request_and_confirm` semantics: `B` is the adder/first confirmer entry via `MultiSigRequestWithSigner`, `C` adds explicit confirmation).
4. Members execute a separate, legitimately-confirmed `DeleteMember { member: C }` request (3 confirmations from A, B, D) — `delete_member` removes `C` from `self.members`, but `confirmations[R]` still contains `C`.
5. Member `A` calls `confirm(R)`. `confirmations[R].len() + 1 == 3 >= num_confirmations`, so `execute_request(R)` runs the `Transfer`, even though only `A` and `B` are currently live members who actually endorsed `R` at execution time (`C`'s vote is stale) — the effective live-confirmation count (2) is below the configured threshold (3). [5](#0-4)

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

**File:** multisig/src/lib.rs (L198-216)
```rust
                MultiSigRequestAction::DeleteKey { public_key } => {
                    self.assert_self_request(receiver_id.clone());
                    let pk: PublicKey = public_key.into();
                    // delete outstanding requests by public_key
                    let request_ids: Vec<u32> = self
                        .requests
                        .iter()
                        .filter(|(_k, r)| r.signer_pk == pk)
                        .map(|(k, _r)| k)
                        .collect();
                    for request_id in request_ids {
                        // remove confirmations for this request
                        self.confirmations.remove(&request_id);
                        self.requests.remove(&request_id);
                    }
                    // remove num_requests_pk entry for public_key
                    self.num_requests_pk.remove(&pk);
                    promise.delete_key(pk)
                }
```

**File:** multisig/src/lib.rs (L246-266)
```rust
    /// Confirm given request with given signing key.
    /// If with this, there has been enough confirmation, a promise with request will be scheduled.
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert!(
            !confirmations.contains(&env::signer_account_pk()),
            "Already confirmed this request with this key"
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(env::signer_account_pk());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
    }
```

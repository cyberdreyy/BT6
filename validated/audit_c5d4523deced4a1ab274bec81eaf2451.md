### Title
Stale confirmations from removed multisig members are still counted toward the K‑of‑N threshold, allowing request execution below the live‑member threshold - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm()` in `multisig2/src/lib.rs` counts entries in the persisted `confirmations` set to decide whether a request has reached `num_confirmations`. `delete_member()` only purges confirmations/requests that were *originated* by the removed member; it never scans other pending requests to strip the removed member's *confirmation* entries from their `confirmations` sets. A request can therefore be executed with fewer live (currently‑a‑member) approvals than `num_confirmations` requires, because a stale confirmation from an account that is no longer a member still counts toward the threshold.

### Finding Description
`confirm()` performs the threshold check purely on set size, not on live membership of every prior confirmer: [1](#0-0) 

`assert_valid_request()` only checks that the *current caller* is a member; it never re-validates the membership of the accounts already recorded in `confirmations`: [2](#0-1) 

`delete_member()` removes requests and confirmations only for requests whose *originator* (`r.member`) equals the deleted member. It does not iterate over `confirmations` values (the sets of members who confirmed *other* requests) to remove the deleted member's stale confirmation entries: [3](#0-2) 

Consequently, the intended custody binding — "a request executes only if at least `num_confirmations` *currently live* members approved it" — degrades to "a request executes if the *historical* confirmation set size reaches `num_confirmations`," which can include confirmers who have since been removed from the multisig.

### Impact Explanation
This directly matches the Critical impact category "a multisig request executed below threshold." An attacker (or a set of collaborating members) can get a stale confirmation from a member scheduled for removal, wait for that removal to complete, and then have as few as `num_confirmations - 1` *live* members finish confirming — funds transfer, key addition, or contract upgrade actions (`Transfer`, `AddKey`, `DeployContract`, etc.) then execute despite the current membership never reaching the configured threshold. This can lead to unauthorized movement of NEAR held by the multisig account.

### Likelihood Explanation
This requires no privileged access beyond being a multisig member (or having previously been one) and does not require a redeploy or foundation action — it is triggered purely by the normal `add_request` → `confirm` → `DeleteMember` → `confirm` sequence, which is exactly the documented "Gotchas" pattern already acknowledged in `multisig/README.md` (member removal interacting with active requests) but not remediated for confirmations left on *other* people's requests. Any multisig where membership changes over the lifetime of a pending request is exposed, which is a realistic operational scenario (member rotation, key compromise response, etc.).

### Recommendation
When counting confirmations in `confirm()`, filter `confirmations` to only members currently present in `self.members` before comparing against `num_confirmations`, e.g.:
```rust
let live_confirmations = confirmations
    .iter()
    .filter(|m| self.members.contains(&MultisigMember::from_str_or_similar(m)))
    .count();
if live_confirmations as u32 + 1 >= self.num_confirmations { ... }
```
Alternatively, when a member is deleted, iterate all pending requests' confirmation sets (not just those the member originated) and remove the member's confirmation entry, re-checking each request's remaining confirmations against the threshold.

### Proof of Concept
1. Initialize multisig with members `{A, B, C, D}` and `num_confirmations = 3`.
2. `B` calls `add_request` creating request `X` (e.g., `Transfer` of the account balance to an attacker-controlled address).
3. `A` calls `confirm(X)` → confirmations = `{A}`.
4. `C` calls `confirm(X)` → confirmations = `{A, C}` (2/3, not yet executed).
5. Members execute a separate, already-fully-confirmed request that performs `DeleteMember { member: A }`. This succeeds because `members.len() - 1 (=3) >= num_confirmations (=3)`. Live members are now `{B, C, D}`. Request `X`'s confirmations set is untouched (`delete_member` only cleans requests originated by `A`, and `X` was originated by `B`), so it still contains `{A, C}`.
6. `D` calls `confirm(X)`. `assert_valid_request` passes because `D` is a live member. The check `confirmations.len() as u32 + 1 >= num_confirmations` evaluates `2 + 1 >= 3` → true, so `execute_request` runs the `Transfer` action.
7. Result: request `X` executed with confirmations attributed to `A` (no longer a member), `C`, and `D` — i.e., only 2 of the 3 *live* members (`C`, `D`) actually approved it while being current members, one short of the configured `num_confirmations = 3` threshold.

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

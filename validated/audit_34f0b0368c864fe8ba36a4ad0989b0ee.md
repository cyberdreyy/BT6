### Title
Stale Confirmations From Removed Multisig Members Allow Request Execution Below the Live-Member Threshold - ([File: multisig2/src/lib.rs])

### Summary
`MultiSigContract::confirm()` counts the size of the `confirmations` set for a request against `num_confirmations` to decide whether to execute it, but `delete_member()` never scrubs a removed member's confirmations from *other* pending requests they had already confirmed (only requests they *authored* are purged). As a result, a request can be executed using a confirmation credited to an account/key that is no longer a multisig member, meaning fewer *live* members actually approved the transfer than the configured threshold.

### Finding Description
`confirm()` in [1](#0-0)  executes the request as soon as `confirmations.len() + 1 >= self.num_confirmations`. It never re-validates that every already-recorded confirmer in that set is still a current member of `self.members` — it only checks that the *current* caller (`current_member()`) is a member.

`delete_member()`, invoked via `MultiSigRequestAction::DeleteMember` in `execute_request()`, is the only place that reacts to member removal: [2](#0-1) . It removes outstanding requests *authored by* the removed member (`r.member == member`) and clears `num_requests_pk` for that member, but it does **not** iterate `self.confirmations` to strip the removed member's entry from other requests they had merely confirmed (not authored). Those confirmations remain in the `HashSet<String>` stored per `request_id` forever, or until the request executes/gets deleted.

Binding broken: `confirmations counted == live members who approved`. After a member is removed, this becomes `confirmations counted > live members who approved`, because a phantom confirmation from a now-nonexistent member still counts toward `num_confirmations`.

### Impact Explanation
Because `MultiSigRequestAction::Transfer` (and other privileged actions such as `AddKey`, `FunctionCall`, `DeployContract`) are executed through the same `confirm()`/threshold logic, a request can reach execution with contributions from fewer currently-authorized members than `num_confirmations` requires. For example, with `num_confirmations = 3` and members `{A, B, C, D}`:
1. `A` adds request `R` (Transfer), `B` confirms → `confirmations = {A, B}` (2/3).
2. Members decide to remove `B` (e.g., resignation, suspected key compromise) via a separate `DeleteMember` request; `delete_member()` only removes requests *authored* by `B`, so `R` (authored by `A`) is untouched and `B`'s confirmation on `R` survives.
3. Members are now `{A, C, D}`, and threshold is still 3.
4. `C` confirms `R` → `confirmations = {A, B, C}` = 3, meeting the threshold and executing the transfer — even though `B` is no longer a member and only two *live* members (`A`, `C`) actually approved it.

This is a threshold-below-requirement execution: NEAR can be transferred, keys added, or contracts upgraded with fewer live approvals than the multisig's own security parameter, directly breaking the custody/authorization guarantee the contract is supposed to enforce. This matches the Critical impact category ("a multisig request executed below threshold").

### Likelihood Explanation
This does not require any privileged or malicious action beyond the ordinary, expected multisig workflow of "remove a member and continue processing existing requests" — a routine operational scenario (member rotation, revoking a suspected-compromised signer). Any request that received a confirmation prior to a member's removal, and that is later confirmed to threshold, will silently execute with a stale confirmation counted. The same class of bug exists in the legacy `multisig` contract (`multisig/src/lib.rs`), whose `DeleteKey` handling likewise only purges requests authored by the removed key, not confirmations on other requests — see [3](#0-2) .

### Recommendation
When a member is deleted, iterate all active `confirmations` entries and remove the deleted member's key/account_id from each `HashSet`, or alternatively re-validate at `confirm()` time that every entry in the stored confirmations set is still contained in `self.members` before comparing the count to `num_confirmations` (i.e., recompute the live confirmation count as `confirmations.iter().filter(|m| self.members.contains(m)).count()`).

### Proof of Concept
1. Deploy `multisig2` with `members = [A, B, C, D]`, `num_confirmations = 3`.
2. As `A`, call `add_request` with a `Transfer` action to some `receiver_id` (authored by `A`) → `request_id = R`.
3. As `B`, call `confirm(R)` → `confirmations(R) = {A, B}` (via `MultiSigContract::confirm`, [1](#0-0) ).
4. Using a separate self-request, execute `DeleteMember { member: B }` confirmed by 3 members (`A, C, D`) → `delete_member` runs, per [2](#0-1) ; note `R` is untouched since it was authored by `A`, not `B`.
5. As `C` (now one of 3 live members `{A, C, D}`), call `confirm(R)` → `confirmations(R).len() + 1 == 3 == num_confirmations`, so `execute_request(R)` runs and the transfer executes, even though `B` (whose confirmation is counted) is no longer a member and only `A` and `C` are live approvers.

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

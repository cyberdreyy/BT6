## Confirmed analog: stale confirmations from a removed member/key still count toward the multisig execution threshold

### Title
Removed multisig key/member's prior confirmation still counts toward `num_confirmations`, allowing request execution below live-member threshold - (`multisig/src/lib.rs`, `multisig2/src/lib.rs`)

### Summary
The reported bug class is "a binding is not updated after an identity/ownership change, so a stale reference (previous creator) keeps being trusted instead of the new authorized party." The equivalent custody binding in this repository's multisig contracts is: `confirmations counted for a request` should equal `confirmations from currently-live members/keys`. When a key (`multisig`) or member (`multisig2`) is deleted, the contract only purges confirmation records for requests **created (signed)** by that key/member — it does not purge that key/member's confirmation **entries left on other requests it had already confirmed**. A stale confirmation from a now-removed signer therefore still counts toward the `num_confirmations` threshold.

### Finding Description
In `multisig/src/lib.rs`, `execute_request`'s `DeleteKey` handling only cleans up requests where `r.signer_pk == pk` (i.e., requests *authored* by the deleted key): [1](#0-0) 

It never scans `self.confirmations` to strip the deleted `pk` from confirmation sets of requests authored by *other* keys that this key may have already confirmed via `confirm()`.

`confirm()` simply grows the `HashSet<PublicKey>` for the request and compares its length to `num_confirmations`: [2](#0-1) 

There is no re-validation at confirm-time (or at delete-time) that every entry in `confirmations` still corresponds to a currently valid access key on the account. The same pattern exists in `multisig2/src/lib.rs`: `delete_member` only removes requests authored by the deleted member, [3](#0-2) 
and `confirm()` again just counts set membership against `num_confirmations` without checking current member status of prior confirmers: [4](#0-3) 

**Binding that should hold (equality broken):**
`confirmations(request_id).len()` at execution time should equal `|{pk ∈ confirmations(request_id) : pk is a currently live key/member}|`. Instead, `confirmations(request_id)` can retain entries for keys/members that have since been deleted, so the counted confirmations can exceed the count of live confirmers.

### Impact Explanation
This is the direct analog of the M-11 root cause: an authorization/custody record (`confirmations`) is not fully updated when the underlying identity binding changes (member/key removed), so a party no longer entitled to authorize (a removed key/member) still contributes to executing privileged actions (`Transfer`, `FunctionCall`, `AddKey`, `DeployContract`, etc.) on behalf of the account. This maps to the "Critical - a multisig request executed below threshold" impact category: a request can be pushed to execution with fewer live confirmers than `num_confirmations`, meaning transfers of NEAR/wNEAR or contract upgrades can be authorized by a quorum computed against a stale confirmer set.

### Likelihood Explanation
Requires: (1) a request pending with fewer than `num_confirmations` confirmations, one of which comes from key/member X; (2) X being removed via a separate `DeleteKey`/`DeleteMember` request (a normal member-rotation operation, not requiring any privileged/out-of-scope actor); (3) enough remaining live members confirming the original request to reach `num_confirmations` counting X's stale entry. This is a plausible operational sequence (member rotation is a documented, expected multisig operation) and does not require a victim key, foundation, or redeploy — it only requires the normal `add_request`/`confirm`/`DeleteKey`/`DeleteMember` flow available to legitimate members, making the "removed member's approval still valid" outcome an unintended consequence of ordinary usage.

### Recommendation
When executing `DeleteKey` (`multisig`) or `DeleteMember` (`multisig2`), iterate over **all** outstanding requests' confirmation sets (not only those authored by the removed key/member) and remove the deleted key's/member's entry from each. Alternatively, at `confirm()`/execution time, filter `confirmations(request_id)` to only those keys/members that are still valid (present in the active key set for `multisig`, or `self.members` for `multisig2`) before comparing against `num_confirmations`.

### Proof of Concept
1. Multisig configured with `num_confirmations = 3` and keys `K1, K2, K3, K4`.
2. `K1` calls `add_request` (creates request `R` with an action, e.g. `Transfer`). `confirmations[R] = {}`.
3. `K2` calls `confirm(R)` → `confirmations[R] = {K2}` (1 < 3, not executed).
4. Separately, `K1` and two others confirm a `DeleteKey{public_key: K2}` request, which executes: `execute_request`'s `DeleteKey` branch only removes requests where `signer_pk == K2` — since `R` was authored by `K1`, `R`'s confirmation set is untouched, so `confirmations[R]` still equals `{K2}` even though `K2` is no longer a valid access key on the account (per `multisig/src/lib.rs:198-216`).
5. `K3` calls `confirm(R)` → `confirmations[R] = {K2, K3}`, length 2 (< 3, still not executed with this exact threshold, but illustrates accumulation of a stale entry).
6. `K4` calls `confirm(R)` → `confirmations[R].len() + 1 = 3 >= num_confirmations` → `execute_request(R)` is invoked and the transfer is executed, even though only `K1` (initiator, uncounted directly but implicit), `K3`, and `K4` are live confirming keys plus a stale `K2` entry — i.e., the request executes having counted a confirmation from a key that was deleted from the account before the quorum was reached, per the `confirm` logic at `multisig/src/lib.rs:246-266`.

This demonstrates a request can reach `num_confirmations` while including a stale, no-longer-valid confirmer, breaking the intended `K`-of-`N` live-member authorization guarantee.

### Citations

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

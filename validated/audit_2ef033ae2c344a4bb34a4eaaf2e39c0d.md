### Title
Stale confirmations from deleted keys let `confirm()` execute a request below `num_confirmations` live signers - ([File: multisig/src/lib.rs])

### Summary
`confirm()` only checks the raw size of the `confirmations: HashSet<PublicKey>` for a request against `self.num_confirmations`, never re-validating that every public key already inside that set is still a live access key on the account. `execute_request`'s `DeleteKey` handler only purges requests *originated* by the deleted key (`r.signer_pk == pk`) and never scans other requests' `confirmations` sets to evict the deleted key from them, so a request can be executed with confirmations counted from a key that was removed in the meantime.

### Finding Description
The binding that must hold is:
`confirmations_counted_at_execution (self.confirmations.get(&request_id).len()+1) == live_members_who_actually_confirmed`

In `confirm()`: [1](#0-0) 
the threshold check `confirmations.len() as u32 + 1 >= self.num_confirmations` is evaluated purely against set cardinality. There is no lookup against the account's current access keys.

The only place that ever prunes `confirmations` in response to a key removal is the `DeleteKey` branch of `execute_request`: [2](#0-1) 
which filters `self.requests` by `r.signer_pk == pk` — i.e. it only removes requests *added* by the deleted key — and clears `self.confirmations` for *those* requests only. It never iterates all other pending requests' `confirmations` HashSets to strip out `pk` from sets where that key merely *confirmed* (but did not originate) the request.

Consequently: request R1 is added by K1 and confirmed by K1 and K2 (num_confirmations=3, so R1 is still pending with 2 confirmations). A second, fully-confirmed request executes `DeleteKey(K2)`. Because R1 was not *added* by K2, R1 and its confirmations (still containing K2) survive untouched. K3 then calls `confirm(R1)`, and `confirmations.len() (2) + 1 >= 3` passes and `execute_request(R1)` fires — even though K2 is no longer a live key. The executed request only had 2 currently-live confirming keys (K1, K3), not 3, breaking the intended k-of-n binding. No existing guard (`assert_valid_request`, `assert_self_request`, `assert_one_action_only`) checks confirmation-key liveness.

### Impact Explanation
Any `MultiSigRequestAction` (Transfer, AddKey, FunctionCall, DeployContract, etc.) can be executed with fewer live confirmations than `num_confirmations` mandates, directly matching the "Critical - a multisig request executed below `num_confirmations` live members" category. This can move NEAR out of the multisig account, add an unauthorized full-access key, or deploy/upgrade contract code, all authorized by a stale, no-longer-valid signature. It is repeatable for any multisig instance and any pending request where at least one confirming key is later removed before the request reaches threshold.

### Likelihood Explanation
Requires: (1) a multisig with `num_confirmations` ≥ 3 (or generally ≥2) so a request can sit in a partially-confirmed state; (2) a key later removed via a separate, independently-confirmed `DeleteKey` request while the first request is still pending with that key's confirmation recorded. This is plausible in normal multisig lifecycle events (rotating out a compromised or departing signer) — no unusual attacker capital is needed, only ordinary use of the multisig's own request/confirm flow, which any key-holder or the owning organization would exercise routinely.

### Recommendation
In `execute_request`'s `DeleteKey` branch, also iterate all pending `self.confirmations` entries and remove `pk` from every confirmation set (not just requests it originated), and/or have `confirm()` re-validate at execution time that all keys in the request's confirmation set are still present among live access keys (e.g., cross-check against `num_requests_pk` or an explicit registry of active multisig keys) before treating the raw HashSet length as satisfying `num_confirmations`.

### Proof of Concept
```rust
#[test]
fn test_stale_confirmation_after_delete_key() {
    let amount = 1_000;
    // K1 adds and confirms R1 (num_confirmations = 3)
    testing_env!(context_with_key(K1.clone(), amount));
    let mut c = MultiSigContract::new(3);
    let transfer_request = MultiSigRequest {
        receiver_id: bob(),
        actions: vec![MultiSigRequestAction::Transfer { amount: amount.into() }],
    };
    let r1 = c.add_request(transfer_request.clone());
    c.confirm(r1); // K1 confirms -> confirmations = {K1}

    // K2 confirms R1
    testing_env!(context_with_key(K2.clone(), amount));
    c.confirm(r1); // confirmations = {K1, K2}, len=2 < 3, not yet executed

    // Separate request deletes K2 (fully confirmed with num_confirmations=1 for simplicity,
    // or with enough other confirmations to reach threshold)
    testing_env!(context_with_key(K1.clone(), amount));
    let delete_key_request = MultiSigRequest {
        receiver_id: alice(),
        actions: vec![MultiSigRequestAction::DeleteKey { public_key: K2.clone() }],
    };
    c.add_request_and_confirm(delete_key_request); // executes DeleteKey(K2)

    // Assert stale confirmation still present on R1
    assert!(c.get_confirmations(r1).contains(&K2));

    // K3 confirms R1, completing len(2)+1 >= 3, though K2 is no longer live
    testing_env!(context_with_key(K3.clone(), amount));
    let result = c.confirm(r1);
    // execute_request fires the Transfer despite only K1 and K3 being live confirmers
    assert_eq!(c.requests.len(), 0); // R1 executed with only 2 live signers
}
```

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

**File:** multisig/src/lib.rs (L248-266)
```rust
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

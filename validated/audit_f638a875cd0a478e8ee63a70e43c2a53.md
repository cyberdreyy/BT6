### Title
Stale confirmations from a deleted key still count toward `num_confirmations`, allowing execution below the live-member threshold - (`multisig/src/lib.rs`)

### Summary
`MultiSigContract::confirm` counts every public key ever recorded in a request's `confirmations: HashSet<PublicKey>`, but `execute_request`'s `DeleteKey` handler only purges confirmations/requests whose **original requester** (`signer_pk`) was the deleted key — it never scans other pending requests for confirmations *cast by* that key. A key that confirms a request and is later removed still has its confirmation counted, letting the request execute with fewer live-member approvals than `num_confirmations` requires.

### Finding Description
The invariant that should hold is:
`confirmations_counted(request_id) == |{ pk ∈ confirmations(request_id) : pk is still a valid access key on the account }|`

In practice, `confirm` just uses the raw stored set: [1](#0-0) 

`confirmations` is a persistent `HashSet<PublicKey>` per request, populated purely by whichever `env::signer_account_pk()` called `confirm` in the past: [2](#0-1) 

When a key is removed via `MultiSigRequestAction::DeleteKey`, the cleanup logic only removes *requests originally created* by that key (`r.signer_pk == pk`) and their confirmations — it does not scan `self.confirmations` for entries where the deleted key appears as a *confirmer* on requests created by someone else: [3](#0-2) 

Exploit flow (num_confirmations = 3, keys A, B, C, D on the account):
1. Key A calls `add_request` for a `Transfer` (`request_id_x`); confirmations = `{}`.
2. Key B calls `confirm(request_id_x)`; confirmations = `{B}` (1 < 3, not executed).
3. Key A creates a separate `DeleteKey{public_key: B}` request (`request_id_y`); keys C and D confirm it, reaching 3 confirmations and executing `DeleteKey(B)`. The cleanup filters `self.requests` for `signer_pk == B`; `request_id_x` was signed by A, so it is untouched — B's stale confirmation on `request_id_x` survives.
4. Key C calls `confirm(request_id_x)`; confirmations = `{B, C}` (2 < 3, not yet executed).
5. Key D calls `confirm(request_id_x)`; confirmations.len() + 1 = 3 >= 3 → `execute_request` fires the `Transfer`, even though B's key no longer exists on the account. Only C and D are actually live confirmers — the transfer executed with 2 live approvals against a 3-of-n policy.

`assert_valid_request` and `confirm` never re-validate that each public key in the stored `confirmations` set still corresponds to an existing access key; they only check that the *caller* is `current_account_id` and that the request/confirmations entries exist: [4](#0-3) 

No existing guard (`assert_self_request`, `assert_one_action_only`, `assert_valid_request`) checks this, so the stale-confirmation counting is unmitigated.

### Impact Explanation
This directly matches the Critical category "a multisig request executed below `num_confirmations` live members." Any request pending confirmation at the time a co-signer is removed retains that removed signer's vote; a subsequent, smaller-than-required set of still-valid keys can push the request past threshold and move funds, add/delete keys, or deploy code on the account — i.e., NEAR leaves (or account control changes on) the multisig account without the policy-mandated quorum of currently authorized keys. This is repeatable for every key rotation event where a request was left pending across the rotation, and works against any multisig deployed from this factory/contract.

### Likelihood Explanation
No special privilege beyond being (at some point) a holder of one access key on the multisig account is required, and the "attacker" key can have already been removed by the time the stale confirmation is exploited — it only needs to have confirmed the request *before* removal. This is a normal, expected operational sequence (rotate a compromised or departing member's key while a request is in flight), not an edge case requiring nonce exhaustion; it is trivially reproducible in a handful of transactions/gas and does not depend on `request_nonce` approaching `u32::MAX` at all.

### Recommendation
When executing `DeleteKey`, iterate all entries of `self.confirmations` (not just requests whose `signer_pk` equals the deleted key) and remove the deleted public key from every confirmation set; alternatively, validate at `confirm`/execution time that every public key in a request's confirmation set is still present among the account's current access keys before counting it toward `num_confirmations`.

### Proof of Concept
`cargo test` plan (extends the existing unit tests in `multisig/src/lib.rs`, using `testing_env!`/`VMContextBuilder` as in `add_key_delete_key_storage_cleared`):

1. `MultiSigContract::new(3)` with the account holding four mocked keys A, B, C, D (only `signer_account_pk` needs to differ per call under `testing_env!`).
2. As A: `add_request(Transfer{...})` → `request_id_x`.
3. As B: `confirm(request_id_x)` → assert `c.confirmations.get(&request_id_x).unwrap().len() == 1` and contains B.
4. As A: `add_request(DeleteKey{public_key: B})` (receiver = self) → `request_id_y`.
5. As C then D: `confirm(request_id_y)` twice → executes; assert `c.get_num_requests_pk(B) == 0` and `c.requests.get(&request_id_y).is_none()`.
6. Assert (the broken binding) `c.confirmations.get(&request_id_x).unwrap().contains(&B_key)` is still `true` even though B has just been deleted — i.e. `live_valid_confirmations(request_id_x) = 1` (just C after step 5... actually B is stale) while `stored_confirmations(request_id_x).len() = 1` still includes B.
7. As C: `confirm(request_id_x)` → assert `confirmations.len() == 2` (`{B, C}`), request not yet executed.
8. As D: `confirm(request_id_x)` → assert the request executes (`c.requests.get(&request_id_x).is_none()` and `c.confirmations.get(&request_id_x).is_none()`) despite only C and D being live confirmers (2 of the current membership), proving execution occurred with `live_confirmations(request_id_x) = 2 < num_confirmations = 3`.

### Citations

**File:** multisig/src/lib.rs (L82-89)
```rust
    num_confirmations: u32,
    request_nonce: RequestId,
    requests: UnorderedMap<RequestId, MultiSigRequestWithSigner>,
    confirmations: UnorderedMap<RequestId, HashSet<PublicKey>>,
    num_requests_pk: UnorderedMap<PublicKey, u32>,
    // per key
    active_requests_limit: u32,
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

**File:** multisig/src/lib.rs (L293-310)
```rust
    fn assert_valid_request(&mut self, request_id: RequestId) {
        // request must come from key added to contract account
        assert_eq!(
            env::current_account_id(),
            env::predecessor_account_id(),
            "Predecessor account must much current account"
        );
        // request must exist
        assert!(
            self.requests.get(&request_id).is_some(),
            "No such request: either wrong number or already confirmed"
        );
        // request must have
        assert!(
            self.confirmations.get(&request_id).is_some(),
            "Internal error: confirmations mismatch requests"
        );
    }
```

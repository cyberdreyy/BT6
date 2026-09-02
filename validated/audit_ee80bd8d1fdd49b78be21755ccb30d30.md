### Title
Stale confirmations from removed multisig members still count toward `num_confirmations`, allowing execution below threshold - (File: multisig/src/lib.rs, multisig2/src/lib.rs)

### Summary
Both `multisig/src/lib.rs` and `multisig2/src/lib.rs` implement a k-of-n multisig by storing a `HashSet` of confirming identities (public key in `multisig`, member string in `multisig2`) per pending request, and comparing the set's size against `num_confirmations` in `confirm()`. When a member is removed (`DeleteKey` in `multisig`, `DeleteMember` in `multisig2`), the code purges only requests that member originally *created* (`r.signer_pk == pk` / `r.member == member`), but does **not** scrub that member's stale confirmations from any *other* pending request they had previously confirmed but did not create. Those stale confirmations remain in the `confirmations` set and continue to count toward quorum for that other request even after the confirming identity is no longer a member of the multisig.

### Finding Description
The binding this contract is supposed to enforce is: `count(confirmations for request R at execution time) == count(currently-live members who approved R)`. The implementation instead computes `count(confirmations for request R) == count(entries ever inserted into the HashSet, regardless of member liveness)`.

In `multisig/src/lib.rs`, `confirm()` checks:
```
if confirmations.len() as u32 + 1 >= self.num_confirmations {
``` [1](#0-0) 

`DeleteKey` execution only cleans up requests whose *creator* (`signer_pk`) is the key being removed:
```
let request_ids: Vec<u32> = self
    .requests
    .iter()
    .filter(|(_k, r)| r.signer_pk == pk)
    .map(|(k, _r)| k)
    .collect();
for request_id in request_ids {
    self.confirmations.remove(&request_id);
    self.requests.remove(&request_id);
}
``` [2](#0-1) 

This filter is keyed on `signer_pk` (the request's *creator*), not on membership in the `confirmations` `HashSet` for other requests. Consequently, if key `K1` confirmed a *different* pending request `R` (created by `K2`), and `K1` is later removed via a legitimate `DeleteKey` action, `R`'s stored confirmation set still contains `K1`. When a currently-valid member later confirms `R`, `K1`'s stale confirmation is counted alongside the live confirmation, allowing `R` to reach `num_confirmations` and execute even though only one currently-authorized member actually approved it at that point in time.

The identical pattern exists in `multisig2/src/lib.rs`'s `delete_member()`, which filters by `r.member == member` (the request's creator/member field), again missing confirmations recorded by that member on requests created by someone else:
```
let request_ids: Vec<u32> = self
    .requests
    .iter()
    .filter_map(|(k, r)| if r.member == member { Some(k) } else { None })
    .collect();
``` [3](#0-2) 

The `confirmations` LookupMap/HashSet for every *other* request that the removed member had confirmed is left untouched.

### Impact Explanation
This breaks the multisig's core custody guarantee that a request (including a `Transfer` of NEAR funds, `AddKey`/`AddMember` granting access, or `FunctionCall` moving assets) requires `num_confirmations` currently-authorized signers. A request can execute with fewer than `num_confirmations` *live* approvals, because a stale confirmation left behind by a departed/removed member is counted as if it were still valid consent. This matches the Critical impact class "a multisig request executed below threshold," since NEAR funds or account authority can move on the strength of fewer real, currently-trusted approvers than the configured policy requires.

### Likelihood Explanation
This requires no external attacker at all beyond an ordinary multisig lifecycle event: a pending unconfirmed/under-confirmed request plus a legitimate, properly-authorized removal of a member who had confirmed a different pending request. Multisig membership churn (removing departing employees/keys) is an expected, routine operation for these contracts, so the precondition is easily met in normal operation, not a contrived edge case.

### Recommendation
When removing a member/key (`DeleteKey` in `multisig`, `DeleteMember` in `multisig2`), iterate over **all** pending requests' confirmation sets (not just requests created by that member) and remove the departing identity from each. Alternatively, re-validate at `confirm()`/execution time that every entry in a request's confirmation set still corresponds to a current member before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy `multisig` with `num_confirmations = 2` and access keys `K1, K2, K3` all added as multisig members.
2. `K2` calls `add_request` to create request `R` (e.g., `Transfer` of contract funds to an arbitrary receiver). `R.signer_pk = K2`.
3. `K1` calls `confirm(R)`. `confirmations(R) = {K1}` (count 1 < 2, pending).
4. Separately, the team legitimately approves removing `K1` (e.g., because K1 left): a `DeleteKey{public_key: K1}` request is created and confirmed by `K2` and `K3` to reach `num_confirmations = 2`. On execution, `execute_request` only purges requests where `r.signer_pk == K1`; since `R.signer_pk = K2`, `R` is untouched, so `confirmations(R)` still equals `{K1}`. `K1`'s access key is deleted from the account via `promise.delete_key(K1)`.
5. `K3` (a still-valid member) calls `confirm(R)`. `confirmations.len() + 1 = 2 >= num_confirmations (2)`, so `R` executes — the `Transfer` action fires — even though only `K3` is a currently live, authorized signer approving it at that moment; `K1`'s approval is stale.

This was traced statically in `multisig/src/lib.rs` (`confirm`, `execute_request`'s `DeleteKey` branch) and the analogous `multisig2/src/lib.rs` (`confirm`, `delete_member`); dynamic execution against a deployed contract was not performed as part of this review.

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

**File:** multisig2/src/lib.rs (L356-371)
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
```

## Title
Confirmations from removed multisig members remain counted toward execution threshold - (File: `multisig/src/lib.rs`, `multisig2/src/lib.rs`)

## Summary
This is the closest in-scope analog to the report's `isIncreasing` bug class: a binding that is supposed to be checked against the *current* state (live members) is instead checked against a stale/partial snapshot (confirmations recorded earlier), letting a request execute with fewer effectively-authorized confirmations than the configured threshold.

## Finding Description
`MultiSigContract::confirm` decides whether to execute a request purely by comparing the size of the stored `confirmations` set to `num_confirmations`: [1](#0-0) 

Confirmations are added by public key (`multisig`) or by `MultisigMember` (`multisig2`) and persist in the `confirmations` map indefinitely once inserted, independent of whether the confirming key/member is later removed from the multisig - **except** when the removed signer happens to be the *creator* of a request. The `DeleteKey` handler only purges requests whose `signer_pk` (the request's creator) equals the deleted key; it does not scan other pending requests for confirmations contributed by that key: [2](#0-1) 

The same asymmetry exists in `multisig2`, where `DeleteMember` removes the member from the `members` set but the `confirm` threshold check only compares confirmation-set cardinality to `num_confirmations`, with no re-validation that each recorded confirmation still belongs to a current member: [3](#0-2) 

Consequently, if member/key A confirms a pending request created by member/key B, and A is subsequently removed via `DeleteKey`/`DeleteMember` (a separate, already-executed multisig action), A's confirmation stays in the `confirmations` set for B's request. When a later confirmation from a still-valid member pushes `confirmations.len() + 1 >= num_confirmations`, the request executes even though the actual number of *live* confirming members is one less than `num_confirmations`.

This mirrors the reported bug precisely: `isIncreasing()` checked `sizeDelta`'s sign against the pre-state size but never re-validated the sign of the *resulting* state (`self.size + sizeDelta`), so a flip could satisfy a stale-derived condition. Here, `confirm()` checks the confirmation-count binding against the stale recorded set without re-validating that each of its members is still live, so a membership flip (removal) can satisfy a stale-derived threshold condition.

## Impact Explanation
This breaks the core custody/authorization guarantee of the multisig: "a request executed below threshold" of live, still-authorized confirmers. A request (e.g. `Transfer`, `FunctionCall`, `AddKey`) can execute funds movement or privileged operations with effectively fewer than `num_confirmations` currently-valid approvals, i.e. NEAR moved (or a privileged action performed) by a set of confirmations that does not actually meet the configured security threshold. This matches the "Critical" impact class: a multisig request executed below threshold.

## Likelihood Explanation
Requires: (1) at least one confirmation on a pending request, (2) a subsequent `DeleteKey`/`DeleteMember` action removing that confirmer (itself requiring the normal threshold to pass, but done as an unrelated action), and (3) the original request later reaching threshold via other confirmers before being deleted/expired. This is a realistic operational sequence for any multisig doing routine key rotation while other requests are in flight, and requires no privileged victim key or malicious node — only ordinary multisig usage patterns. It does not depend on a deployment ignoring documented initialization; it is inherent to `confirm`/`execute_request`'s bookkeeping.

## Recommendation
When executing `DeleteKey`/`DeleteMember`, also strip the deleted key/member from every entry in `confirmations` (not just requests it created), or alternatively re-validate at `confirm()` time that every public key/member in the stored confirmation set is still a current signer/member before comparing the count to `num_confirmations`.

## Proof of Concept
1. Multisig initialized with `num_confirmations = 2`, keys `{A, B, C}`.
2. B creates request `R` (`add_request`).
3. A calls `confirm(R)` → `confirmations[R] = {A}` (1 < 2, not yet executed).
4. Separately, C creates and confirms request `D = DeleteKey{public_key: A}`, which reaches threshold (assume C + B confirm) and executes, removing A's access key. Because `DeleteKey`'s cleanup only removes requests where `signer_pk == A` (i.e., requests A itself created), `confirmations[R] = {A}` is untouched.
5. B calls `confirm(R)` → `confirmations[R].len() + 1 = 2 >= num_confirmations (2)` → `R` executes, even though A is no longer a valid signer and only B is currently a live confirmer. [1](#0-0) [2](#0-1)

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

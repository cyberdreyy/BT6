### Title
Stale confirmations from revoked multisig keys still count toward the approval threshold - ([File: multisig/src/lib.rs])

### Summary
When a key is removed from the NEAR multisig contract via `DeleteKey`, the contract only wipes confirmations for requests that the removed key itself *created*; confirmations that the removed key had already cast on *other* still-pending requests (created by a different key) remain in the `confirmations` set and continue to count toward `num_confirmations`. This breaks the binding "confirmations counted == live/authorized members that approved," analogous to the oracle report's core defect where a value recorded in one epoch is trusted for later decisions without being revalidated against current, legitimate state.

### Finding Description
`MultiSigContract::confirm()` only checks the *size* of the `confirmations: UnorderedMap<RequestId, HashSet<PublicKey>>` set for a given request against `self.num_confirmations`; it never re-validates that every `PublicKey` already present in that set still belongs to a currently authorized signer: [1](#0-0) 

The only cleanup performed on `DeleteKey` is scoped to requests whose *creator* (`signer_pk`) equals the deleted key — it purges `self.confirmations` and `self.requests` for those specific requests, and clears `num_requests_pk` for that key: [2](#0-1) 

This cleanup logic does **not** search `self.confirmations` for entries where the deleted key appears as a *confirmer* on some other, still-open request created by a different signer. Confirmations are recorded purely by `env::signer_account_pk()` at the time of the `confirm()` call: [3](#0-2) 

Concretely:
1. Key A creates Request X (Transfer). Key B confirms Request X (adds B's pubkey to `confirmations[X]`), but the threshold (`num_confirmations`) is not yet reached, so Request X stays pending.
2. Key B is later removed from the multisig via a `DeleteKey{public_key: B}` request that itself has nothing to do with Request X (Request X was created by A, not B), so the cleanup loop in `execute_request` (`filter(|(_k, r)| r.signer_pk == pk)`) does not touch Request X's confirmation set.
3. Request X still shows B's confirmation in `self.confirmations[X]`, even though B's access key has been deleted from the account and B is no longer an authorized signer.
4. If enough other keys later confirm Request X, `confirmations.len() + 1 >= self.num_confirmations` can be satisfied while counting the removed key B as one of the required approvers, so the request executes (e.g., a `Transfer`) with effectively fewer live, currently-authorized signers than the configured threshold requires.

The equality broken is: `confirmations recorded for a pending request == currently live/authorized signer approvals`. After a `DeleteKey` unrelated to the specific request, this equality silently becomes false, yet `confirm()`/`execute_request()` treat the stale count as valid.

### Impact Explanation
This lets an M-of-N multisig execute a `Transfer`, `AddKey`, `DeployContract`, or other privileged `MultiSigRequestAction` with fewer than M currently-authorized signatures, because one of the counted "confirmations" belongs to a key that has since been revoked. This directly undermines the threshold-of-authorization guarantee the contract is built to provide — funds can move (`Transfer`) or the account can be reconfigured (`AddKey`/`DeployContract`) under authorization weaker than the account's own stated policy (`num_confirmations`). Per the stated impact classes, this is a multisig request executed below the intended threshold, which is a Critical-class custody/authorization binding violation.

### Likelihood Explanation
This does not require any special privilege beyond being a normal multisig member with a valid access key at confirmation time: any member can confirm a pending request, and the vulnerability manifests naturally whenever a key is deleted after confirming — but before executing — some other pending request. Deleting keys is a routine, expected multisig operation (e.g. member rotation, revoking a compromised key), so the sequence "confirm a request, then get revoked, then the stale confirmation is later combined with fresh confirmations to reach threshold" is a realistic operational scenario, not a contrived edge case, and requires no owner/foundation collusion or malicious validator — only the ordinary interplay of two legitimate, unprivileged multisig members' actions.

### Recommendation
When executing `DeleteKey` (or in `multisig2`, `DeleteMember`), iterate over `self.confirmations` for *every* pending request (not only requests created by the deleted key) and remove the deleted key/member from each confirmation set. Alternatively, when tallying confirmations in `confirm()`, re-validate that each already-recorded confirming public key/member is still present among current multisig keys/members before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy `MultiSigContract::new(2)` with keys A, B, C (2-of-3 threshold).
2. Key A: `add_request_and_confirm(Transfer{amount, receiver_id})` → Request X now has confirmations `{A}` (1/2).
3. Key B: `confirm(X)` → confirmations `{A, B}` reach 2/2? — instead, arrange so B's confirmation is the *second-to-last* needed, i.e. contract's threshold is 3-of-4: A creates+confirms X (`{A}`), B confirms X (`{A,B}`, 2/3, not yet executed).
4. Key A creates and self-confirms a second request `DeleteKey{public_key: B}` and gets a third key (say D, if using 4 total keys with threshold 3) to confirm it, so it executes: `execute_request` removes B's key/entries only from requests where `signer_pk == B` — Request X (created by A) is untouched, so `confirmations[X]` still equals `{A, B}`.
5. Key C, though B is now deleted from the account, calls `confirm(X)` → `confirmations.len() + 1 == 3 >= num_confirmations(3)` succeeds and `execute_request` runs the `Transfer`, using B's now-stale confirmation as one of the three "confirmations," even though only A and C are still live authorized signers at execution time. [2](#0-1) [1](#0-0)

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

### Title
Stale confirmations from a revoked multisig key still count toward the execution threshold, allowing a request to execute below the live-member threshold - (File: `multisig/src/lib.rs`)

### Summary
This is the closest reachable analog to the `twAML`/`OTAP` griefing bug in this repository. In the reference finding, a user's influence on a shared aggregate (`twAML` weights) survives the removal path (`exitPosition`/burn) because the code that "un-registers" a party's contribution only fires along one specific path, and an attacker can sidestep it. The NEAR `MultiSigContract` has the same shape: a confirmation recorded by a public key is only cleaned up from a request if that key happens to be the *original requester* of that specific request (`DeleteKey` handler in `execute_request`). Confirmations that a key placed on requests it did not originate are never revoked, so a key that has been formally removed from the multisig can still count toward `num_confirmations` on any other pending request, allowing that request to execute with fewer currently-authorized signers than the configured threshold.

### Finding Description
`confirm()` stores each confirming signer's raw public key into a `HashSet<PublicKey>` per request and compares the set's size against `self.num_confirmations`: [1](#0-0) 

When a `DeleteKey` action is executed (e.g., to formally revoke a compromised or departing signer), the cleanup logic only removes **requests originated by that key** (`r.signer_pk == pk`) and the `num_requests_pk` counter for that key. It never scans `self.confirmations` to strip that key's public key out of confirmation sets belonging to requests it did not create: [2](#0-1) 

`confirm()`'s validity check (`assert_valid_request`) only verifies the request/confirmations entries exist — it never re-validates that every public key already present in the confirmations set is still a live access key on the account (this is architecturally impossible for the contract to check on its own, as acknowledged in the repo's own "Gotchas" section, but that section addresses a different scenario — total keys falling below threshold — not stale confirmations remaining valid): [3](#0-2) [4](#0-3) 

**Binding broken:** the number of confirmations counted for a request should equal the number of *currently authorized* signers who approved it (`confirmations.len() == |{live members who confirmed}|`). After a `DeleteKey` execution, this equality can break: `confirmations.len()` can include a public key that is no longer a member of the multisig, so the request executes once `confirmations.len() + 1 >= num_confirmations` even though the count of *live* approvers is strictly less than `num_confirmations`.

### Impact Explanation
This falls under the Critical category explicitly listed: "a multisig request executed below threshold." A revoked signer's stale approval can push an unrelated, pending request (e.g., a `Transfer` or `AddKey` action) over the confirmation threshold using fewer than the configured number of live, authorized keys. Since `execute_request` can transfer funds, add full-access keys, or deploy contracts, this directly breaks the custody/authorization guarantee the multisig is meant to enforce.

### Likelihood Explanation
This does not require any privileged access or victim key beyond the normal, expected multisig workflow: (1) a currently-valid key confirms request X but X doesn't reach threshold yet, (2) that key is later revoked via a legitimate `DeleteKey` request (routine key rotation/offboarding — a common, expected operational event, not an attack on its own), (3) X is still pending and retains the old key's confirmation. Any of the remaining members (or an outsider colluding with fewer than `num_confirmations` of the current members) can then push X over the threshold with fewer live confirmations than intended. No malicious deployment, foundation privilege, or social engineering is needed — it only needs normal contract usage plus the ordinary act of rotating out a key while a request is in flight.

### Recommendation
When executing `DeleteKey`, iterate over all entries in `self.confirmations` (not just requests where `signer_pk == pk`) and remove the deleted public key from every confirmation set. Alternatively, validate each confirmation entry against `env::is_valid_access_key` equivalents at `confirm()`/execution time, or require re-confirmation from all currently active keys whenever the key set changes.

### Proof of Concept
1. Deploy `MultiSigContract::new(3)` with keys `A`, `B`, `C`, `D`.
2. `B` calls `add_request` for a `Transfer` action → request `X` (signer_pk = B).
3. `A` calls `confirm(X)` → confirmations = `{A}` (1/3).
4. Separately, `add_request_and_confirm`/`confirm` a `DeleteKey { public_key: A }` request with 3 of the other keys, revoking `A`'s access key from the account. Since `X.signer_pk == B ≠ A`, `X` is untouched by the cleanup loop in the `DeleteKey` branch; `X`'s confirmations set still contains `A`.
5. `C` calls `confirm(X)` → confirmations = `{A, C}` (2/3).
6. `D` calls `confirm(X)` → `confirmations.len() + 1 == 3 >= num_confirmations` → `X` executes, even though `A` is no longer a valid signer and only `C` and `D` are live approvers alongside the stale `A` entry — i.e., the transfer executes with 2 live confirmations against a nominal threshold of 3.

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

**File:** multisig/src/lib.rs (L292-310)
```rust
    // Prevents access to calling requests and make sure request_id is valid - used in delete and confirm
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

**File:** multisig/README.md (L120-123)
```markdown
### Gotchas
 
User can delete access keys on the multisig such that total number of different access keys will fall below `num_confirmations`, rendering contract locked.
This is due to not having a way to query blockchain for current number of access keys on the account. See discussion here - https://github.com/nearprotocol/NEPs/issues/79.
```

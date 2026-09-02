### Title
Multisig confirmations from a deleted/revoked member remain counted toward the confirmation threshold, allowing a request to execute below the intended K-of-N quorum - (File: `multisig/src/lib.rs`, `multisig2/src/lib.rs`)

### Summary
Both the legacy `multisig` and `multisig2` contracts implement a K-of-N confirmation scheme where a request executes once `confirmations.len() + 1 >= num_confirmations`. When a key/member is removed (`DeleteKey` in `multisig`, `DeleteMember` in `multisig2`), the removal logic only purges *requests originally added by* that key/member, but never scans and strips that key/member's *confirmations already recorded on other still-pending requests*. As a result, a stale confirmation from a since-removed signer continues to count toward the quorum for any request it confirmed before being removed, letting a request execute with fewer currently-authorized confirmations than `num_confirmations` requires.

### Finding Description
In `multisig/src/lib.rs`, `confirm()` adds `env::signer_account_pk()` into the `confirmations: UnorderedMap<RequestId, HashSet<PublicKey>>` set for a request, and checks quorum purely by counting entries in that set: [1](#0-0) 

When a key is deleted via `MultiSigRequestAction::DeleteKey`, the cleanup only removes *requests whose `signer_pk` (the adder) equals the deleted key*, and only then deletes `self.confirmations` for those specific requests. It does not touch confirmation sets belonging to other, still-open requests that the deleted key may have already confirmed: [2](#0-1) 

The same pattern exists in `multisig2/src/lib.rs`: `confirm()` counts membership-string entries in `confirmations: LookupMap<RequestId, HashSet<String>>` toward quorum without re-validating that every counted entry still corresponds to a live member: [3](#0-2)  and `DeleteMember` is only invoked as part of `execute_request`, which has no logic to purge the deleted member's prior confirmations from other pending requests: [4](#0-3) 

The binding this breaks: "confirmations counted" should always equal "confirmations from currently live/authorized members." Concretely, the invariant `|{pk ∈ confirmations(R) : pk is a current member}| >= num_confirmations` is what should gate execution, but the code instead checks `|confirmations(R)| >= num_confirmations` regardless of whether each entry's signer is still a member.

### Impact Explanation
This lets a request execute with effectively fewer than K live confirmations, i.e., a multisig request executed below threshold — explicitly listed as a Critical impact. Concretely:
1. Member/key `B` confirms request `R` (added by member `A`), adding `B`'s pk/id into `confirmations(R)`.
2. The organization later revokes `B` (e.g., `B` is fired, or `B`'s key is suspected compromised) by executing a `DeleteKey`/`DeleteMember` request against `B`.
3. `B`'s stale confirmation on `R` is never removed because the delete logic only clears requests *added by* `B`, not confirmations *given by* `B` on other requests.
4. A remaining member `C` confirms `R`. If `num_confirmations = 2` (or the deleted member's stale vote pushes an N-of-N request over the line), `R` executes using one live confirmation (`C`) plus one dead/stale confirmation (`B`), even though `B` is no longer an authorized signer.
5. `R` can be a `Transfer`, `FunctionCall`, `AddKey`/`AddMember` (granting a new/attacker key control of the account), etc. — the attacker (or the malicious/compromised removed member acting before revocation, or any member who colludes with a soon-to-be-removed member) gets an unauthorized action executed on funds/keys controlled by the multisig account with fewer genuinely live approvals than governance intended.

### Likelihood Explanation
No privileged role beyond "was previously a valid multisig key/member" is required, and key rotation/removal (firing an employee, rotating a compromised key, downsizing the signer set) is a normal, expected multisig operation. Any request left pending across a member-removal event is silently exposed; no special timing exploit or malicious infrastructure trust is needed — the flaw is purely in the `DeleteKey`/`DeleteMember` cleanup path never being extended to strip that signer's confirmations from other pending requests.

### Recommendation
When executing `DeleteKey` (`multisig`) or `DeleteMember` (`multisig2`), iterate over all entries in `self.confirmations` and remove the deleted key's/member's public key or member string from every confirmation set, not just from requests it originally created. Alternatively, validate at `confirm()`-time (or at execution-time, immediately before triggering `execute_request`) that every currently recorded confirmer is still present in the live member/key set, and only count those toward `num_confirmations`.

### Proof of Concept
1. Deploy `multisig` with keys `A`, `B`, `C` and `num_confirmations = 2`.
2. `A` calls `add_request` with a `Transfer` action to an attacker-controlled receiver (`R`).
3. `B` calls `confirm(R)` — `confirmations(R) = {B}`, quorum not yet met (1 < 2), so request stays pending.
4. The organization, unaware `R` is pending, executes a separate fully-confirmed request `DeleteKey { public_key: B }` to revoke `B` (e.g., `B` left the org). Per `execute_request`'s `DeleteKey` handling, only requests where `signer_pk == B` are purged; `R` (added by `A`, merely confirmed by `B`) is untouched, and `confirmations(R)` still contains `B`.
5. `A` calls `confirm(R)`. Since `A ∉ confirmations(R)` yet, this adds `A`, making `confirmations(R).len() = 2 >= num_confirmations (2)`, and `execute_request(R)` fires the `Transfer` — approved by only one currently-live key (`A`) plus one revoked key (`B`), i.e., executed below the real live-member threshold.

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

**File:** multisig2/src/lib.rs (L239-242)
```rust
                MultiSigRequestAction::DeleteMember { member } => {
                    self.assert_self_request(receiver_id.clone());
                    self.delete_member(promise, member)
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

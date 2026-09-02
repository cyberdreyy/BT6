## Title
Removing a multisig member/key does not purge their existing confirmations on other pending requests, allowing a request to execute below the configured confirmation threshold - (File: `multisig/src/lib.rs`, `multisig2/src/lib.rs`)

### Summary
The multisig contracts (`multisig/src/lib.rs` and `multisig2/src/lib.rs`) let `K` members/keys jointly authorize actions via `confirm`. When a member/key is removed via `DeleteKey`/`DeleteMember`, the code only purges **requests originated by** that key/member, not **confirmations they already cast on other still-pending requests**. As a result, a stale confirmation from a now-removed member remains counted toward `num_confirmations`, so a pending request can later be executed with fewer *currently live* members approving it than the configured threshold requires.

### Finding Description
In `multisig/src/lib.rs`, `confirm()` only checks the raw count of confirmations recorded against a request, with no re-validation that every recorded confirming key is still an active key on the account: [1](#0-0) 

When a `DeleteKey` action executes, it removes only the requests **originated by** the deleted key, and clears `num_requests_pk` for that key — but it does not scan `self.confirmations` to strip that key's vote from other requests it confirmed: [2](#0-1) 

The same flaw exists in the newer `multisig2` contract. `delete_member` filters requests by `r.member == member` (the request's *originator*) and removes that member from `self.members`, but never inspects `self.confirmations` to drop the removed member's vote from requests originated by someone else: [3](#0-2) 

`confirm()` in `multisig2` has the same "count confirmations, don't re-validate membership" pattern: [4](#0-3) 

The binding the contract is supposed to guarantee is: `confirmations counted for a request == confirmations from currently-live members`. Once a member is deleted, this equality breaks — the confirmation set for any request they previously confirmed (but did not originate) still contains their vote, silently reducing the number of *live* approvals actually needed to reach `num_confirmations` for that request.

### Impact Explanation
This falls under the "Critical" category: a multisig request executed below threshold. Example: `num_confirmations = 3` with members `{A, B, C, D}`.
- `A` adds request `R` (transfer, or `AddKey`/`FunctionCall`, etc.) and confirms it → confirmations(`R`) = `{A}`.
- `B` confirms `R` → confirmations(`R`) = `{A, B}` (2 of 3, still pending).
- Members then vote (via a separate, properly-threshold-confirmed `DeleteKey`/`DeleteMember` request) to remove `B` (e.g., because `B`'s key is believed compromised, or `B` is being offboarded).
- `B`'s removal purges only requests *originated* by `B`; `R` (originated by `A`) still shows confirmations `{A, B}`.
- `C` now confirms `R` → confirmations(`R`).len() + 1 = 3 ≥ `num_confirmations`, so `R` executes — even though only 2 currently-live members (`A`, `C`) actually approved it; `B`'s vote is stale/dead but still counted.

This lets a request be executed with only `K-1` live approvals instead of the intended `K`, undermining the fundamental threshold guarantee of the multisig (transfers, `FunctionCall`, `AddKey`, etc. can all be pushed through this way).

### Likelihood Explanation
This requires no external attacker privileges beyond being (or having been) one of the legitimate multisig members/keys — exactly the kind of internal-trust-boundary violation the multisig is meant to prevent. Removing members/keys (e.g., key rotation, offboarding a departing signer, revoking a suspected-compromised key) is a normal, expected multisig operation, and any request left pending with a confirmation from the removed party at that time is silently exploitable afterward. No unusual timing or race condition is needed — only that a request be left unconfirmed-to-completion at the moment a confirming member is removed, which is a very plausible, even routine, occurrence in practice (e.g., cooldown periods, `REQUEST_COOLDOWN`, multi-step approvals).

### Recommendation
When executing `DeleteKey` (`multisig/src/lib.rs`) or `DeleteMember` (`multisig2/src/lib.rs`), iterate over all pending requests and remove the deleted key/member's public key/identifier from every request's confirmation set (not just requests it originated). Alternatively, re-validate at `confirm()` time that every entry in the stored confirmation set for a request still corresponds to a currently active key/member before comparing the count against `num_confirmations`.

### Proof of Concept
1. Deploy `multisig` (or `multisig2`) with `num_confirmations = 3` and keys/members `A, B, C, D`.
2. As `A`: `add_request_and_confirm(R)` where `R` is e.g. a `Transfer` request → confirmations(`R`) = `{A}`.
3. As `B`: `confirm(R)` → confirmations(`R`) = `{A, B}` (still pending, needs 1 more).
4. As a separate, fully-confirmed multisig request, execute `DeleteKey{ public_key: B }` (or `DeleteMember` in multisig2) to remove `B`. Verify `B`'s access key/membership is gone, but `R` is still listed in `list_request_ids()` with `get_confirmations(R)` still containing `B`.
5. As `C`: `confirm(R)` → `confirmations.len() + 1 == 3 >= num_confirmations`, so `R` executes, even though only `A` and `C` are currently live confirmers (2 live approvals, not 3). [1](#0-0) [2](#0-1) [3](#0-2)

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

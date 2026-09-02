## Analog Found: Confirmations Counted Against Removed Multisig Keys/Members Are Never Purged

### Title
Stale confirmations from removed multisig keys/members still count toward the execution threshold - (`multisig/src/lib.rs`, `multisig2/src/lib.rs`)

### Summary
The C4 finding is a class of bug where a value that changes mid-flow (the limit-order price) is not re-validated against the condition it was originally checked against (SL/TP), letting a stale approval push execution through. The same pattern exists in both `multisig/src/lib.rs` and `multisig2/src/lib.rs`: when a signing key/member is removed, the contract purges only the *requests that key/member originally added*, but never purges that key/member's *confirmations recorded on other, still-pending requests*. Those stale confirmations continue to count toward `num_confirmations`, so a request can execute even though the number of currently-authorized signers who actually approved it is below the configured threshold.

### Finding Description
`confirm()` in `multisig/src/lib.rs` decides whether to execute a request purely by comparing the *size* of the stored confirmations set to `num_confirmations`, without checking whether each entry in that set still corresponds to a live signing key: [1](#0-0) 

`assert_valid_request()` only checks that the request and confirmations exist — it never verifies that the previously-recorded confirming public keys are still valid members of the multisig: [2](#0-1) 

When a key is removed via `MultiSigRequestAction::DeleteKey`, the cleanup logic only removes requests where the deleted key was the *original signer* (`r.signer_pk == pk`). It does not scan `self.confirmations` for entries where the deleted key appears as a *confirmer* of some other pending request: [3](#0-2) 

The same gap exists in the newer, member-based `multisig2` contract. `confirm()` and `assert_valid_request()` have the identical shape: [4](#0-3) [5](#0-4) 

And `delete_member()` again only removes requests *added by* the removed member (`r.member == member`), leaving that member's confirmations on other pending requests intact: [6](#0-5) 

The equality the contract is supposed to maintain is:
```
confirmations.len() for request R  ==  number of *currently authorized* signers who approved R
```
Because removal only scrubs requests *authored* by the removed key/member and never scrubs confirmations *contributed* by that key/member elsewhere, this equality can be violated: a stale confirmation from a revoked key inflates the count and lets `confirm()` cross the `num_confirmations` threshold using fewer live approvals than intended.

### Impact Explanation
This directly matches the Critical impact category "a multisig request executed below threshold." If a key is compromised (or a member needs to be removed for any reason) after it has already confirmed a pending malicious/unauthorized request (e.g., a `Transfer` or `FunctionCall`), removing that key does not invalidate its earlier confirmation. Once the remaining live confirmations reach `num_confirmations - 1` (i.e., threshold minus the stale one), any single additional live confirmation causes `execute_request()` to run, moving funds or executing an action that was never actually approved by `num_confirmations` currently-trusted parties.

### Likelihood Explanation
This requires a normal, expected operational sequence: a key/member confirms a pending request, is later removed (a routine security response to key rotation/compromise, or simple membership change), and a pending request that key had already confirmed is still outstanding when a new confirmation arrives. No special privilege beyond being (at some point) a valid signer is needed, and no code path anywhere re-validates confirmations against the current member set, so the condition is reachable through ordinary multisig usage rather than a contrived edge case.

### Recommendation
When removing a key (`DeleteKey`) or member (`DeleteMember`), scan `self.confirmations` for *all* pending requests (not just ones the key/member authored) and either strip that key's/member's confirmation from each set or invalidate/re-open the request. Alternatively, `confirm()` should filter `confirmations` down to entries that are still present in the current key/member set before comparing the count to `num_confirmations`.

### Proof of Concept
1. Multisig configured with `num_confirmations = 2`, members `A`, `B`, `C`.
2. `A` calls `add_request` for a malicious `Transfer` request `R1` (no auto-confirm).
3. `B`'s key is compromised; the attacker uses it to call `confirm(R1)` → `confirmations[R1] = {B}` (len 1, below threshold, request stays pending) — see [1](#0-0) .
4. The legitimate owners detect the compromise and submit/execute a `DeleteKey { public_key: B }` request (confirmed by `A` and `C`). The `execute_request` `DeleteKey` branch removes only requests where `B` was the *signer/adder* — `R1` (added by `A`) is untouched, and `confirmations[R1]` still contains `B` — see [3](#0-2) .
5. Later, `A` (or an attacker controlling `A`'s key, e.g. via social engineering into confirming an innocuous-looking request) calls `confirm(R1)` → `confirmations[R1].len() == 2 >= num_confirmations` → `execute_request(R1)` runs the `Transfer`, even though `B` is no longer a valid member and only one currently-live member (`A`) ever knowingly approved `R1`. [6](#0-5)  shows the same purge-only-own-requests pattern in the newer contract, so the identical PoC applies to `multisig2` with `DeleteMember` in place of `DeleteKey`.

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

**File:** multisig2/src/lib.rs (L406-423)
```rust
    /// Prevents access to calling requests and make sure request_id is valid - used in delete and confirm
    fn assert_valid_request(&mut self, request_id: RequestId) {
        // request must come from key added to contract account
        assert(
            self.current_member().is_some(),
            "Caller (predecessor or signer) is not a member of this multisig",
        );
        // request must exist
        assert(
            self.requests.get(&request_id).is_some(),
            "No such request: either wrong number or already confirmed",
        );
        // request must have
        assert(
            self.confirmations.get(&request_id).is_some(),
            "Internal error: confirmations mismatch requests",
        );
    }
```

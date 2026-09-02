Confirmed: this bug is real. Here is the complete finding.

### Title
Stale confirmations from removed multisig keys/members count toward the confirmation threshold, allowing execution below the intended live-signer threshold - (File: multisig/src/lib.rs, multisig2/src/lib.rs)

### Summary
The multisig contract removes an outstanding request's confirmations only when that request is being deleted, executed, or when the request was *authored* by the key/member being deleted. It never scans and prunes stale confirmations that a removed key/member left behind on *other* still-pending requests. Because `confirm()` only checks the size of the stored `confirmations` set (`confirmations.len() as u32 + 1 >= self.num_confirmations`) without verifying that every recorded confirmer is still a valid member, a removed key's earlier confirmation keeps "counting" toward the threshold on any request it previously confirmed but did not author. This lets a request execute with fewer live, currently-authorized confirmations than `num_confirmations` requires.

### Finding Description
In `execute_request`, the `DeleteKey` action (multisig v1) only cleans up requests where the deleted key was the *originating signer*: [1](#0-0) 
It never inspects the `confirmations` map for entries where the deleted `pk` merely *confirmed* (but did not author) a different pending request. The equivalent `delete_member` helper in multisig2 has the identical gap — it filters `requests` by `r.member == member` (the request author) and removes `num_requests_pk`, but never scans `confirmations` sets for the member's stale votes: [2](#0-1) 

The `confirm()` function then trusts the raw cardinality of the stored confirmation set as a proxy for "number of live signers who agreed": [3](#0-2) [4](#0-3) 

`assert_valid_request` only validates that the *caller* confirming is a current member/key holder; it does not re-validate the *existing* entries already stored in the `confirmations` set: [5](#0-4) [6](#0-5) 

This is the same bug class as the CKB advisory: an entity (a transaction / a signer) is removed from the system, but a derived statistic (pool tx-count / confirmation count) that was supposed to track live membership is never decremented, so the aggregate value drifts from ground truth until it produces an incorrect outcome (pool stays "full" / request reaches "threshold").

The equality that should always hold is:
`confirmations_stored_for(request_id) ⊆ current_live_members`

Once a member/key is deleted while having an outstanding confirmation on some other request, this invariant breaks: the stored confirmation count includes an entity no longer entitled to approve transactions on the account.

### Impact Explanation
This crosses an authorization boundary the multisig contract exists to enforce: it degrades a k-of-n multisig into effectively (k-1)-of-(n-1) or worse whenever a removed member had previously confirmed pending requests. An attacker (or compromised key) who confirms several sensitive pending requests (e.g., `Transfer`, `AddKey`, `FunctionCall`) before being detected and removed leaves those stale confirmations in place. The remaining live members, unaware that one of the counted confirmations came from a now-untrusted/removed key, can inadvertently execute a request — including a `Transfer` of NEAR — with one fewer live approval than `num_confirmations` mandates. This matches the "Critical" impact bucket: a multisig request executed below threshold, potentially moving NEAR by parties not fully entitled to authorize it.

### Likelihood Explanation
This requires no special privilege beyond having been a legitimate signer at some point (e.g., a compromised device/key that is later revoked) — a realistic and common operational scenario for multisig wallets (rotating out a compromised or departing signer). No cooperation from other members is needed beyond the ordinary act of confirming the request that later executes; the flaw is purely in bookkeeping, not requiring any social engineering beyond normal key-rotation practice.

### Recommendation
When deleting a key (`DeleteKey`, multisig v1) or a member (`delete_member`, multisig2), iterate over **all** entries in the `confirmations` map (not just requests authored by that signer) and remove the deleted key/member's `PublicKey`/`String` from every confirmation set it appears in. Additionally, consider validating on `confirm()`/execution that every address counted toward `num_confirmations` is still a current member, rather than trusting the raw stored size of the confirmations set.

### Proof of Concept
1. Deploy multisig with `num_confirmations = 3` and members/keys `A, B, C, D`.
2. `C` calls `add_request_and_confirm(request_X)` where `request_X` is `{receiver_id: multisig, actions: [Transfer{amount}]}` — request_X now has confirmations `{C}` (1 of 3).
3. Members detect `C`'s key is compromised and submit+confirm a `DeleteKey{public_key: C}` request using `A`, `B`, `D` (reaching threshold on that separate request). `execute_request`'s `DeleteKey` branch only removes requests *authored* by `C` — `request_X` (authored by `C`, but this example has `C` as author, so pick request_X authored by `D` instead to isolate the bug):
   - Revised: `D` calls `add_request(request_X)` (authored by `D`), `C` confirms it (`confirmations = {C}`), then `C`'s key is deleted via the steps above. Because `request_X` was authored by `D`, not `C`, the `DeleteKey` cleanup loop (`filter(|(_k, r)| r.signer_pk == pk)`) does not touch `request_X`, and `C`'s stale confirmation remains in its `confirmations` set.
4. `A` and `B` now confirm `request_X`. `confirmations.len()` is `{C, A}` then `{C, A, B}` → size 3, `3 + 1 >= 3` triggers on the second confirmation (`{C, A}`.len()=2, +1=3 >= 3) — `request_X` executes the `Transfer` after only 2 live confirmations (`A`, `D`-as-author is irrelevant, `B` never even needed to confirm), despite `C` no longer being a valid signer.
5. Funds move via a request that never received 3 confirmations from currently-authorized signers, only 2 plus one stale/removed one — a multisig request executed below the intended threshold.

### Citations

**File:** multisig/src/lib.rs (L198-215)
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

**File:** multisig2/src/lib.rs (L294-315)
```rust
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

**File:** multisig2/src/lib.rs (L356-379)
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

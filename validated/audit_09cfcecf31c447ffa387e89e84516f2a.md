### Title
Multisig executes a request using stale confirmations from a deleted key, bypassing the live-member threshold - (File: `multisig/src/lib.rs`)

### Summary
The `nuke()` bug class describes a claim/settlement value that is computed against a shared state (`fund`) that can silently change between when a party expects to be paid and when the payout is actually settled, breaking the equality between what was counted and what should have been counted. The analogous binding in `multisig/src/lib.rs` is: `confirmations.len()` for a pending request should always equal the number of confirmations given by **currently valid** (live) signing keys. This binding is broken because deleting a multisig key only purges requests *created* by that key, not that key's stale confirmations left on *other* pending requests, allowing a request to reach `num_confirmations` and execute even though one of the counted confirmations came from a key that is no longer a member of the multisig.

### Finding Description
`confirm()` counts confirmations purely from the `confirmations: UnorderedMap<RequestId, HashSet<PublicKey>>` map and executes the request once the count reaches `num_confirmations`: [1](#0-0) 

When a `DeleteKey` action is executed, the contract removes only the requests whose `signer_pk` (the request's *creator*) equals the deleted key, and clears `num_requests_pk` for that key: [2](#0-1) 

It never scans the other entries of `self.confirmations` to strip the deleted key out of confirmation sets where that key merely **confirmed** (but did not create) a request. As a result, any request that already collected a confirmation from a key before that key was deleted keeps counting that now-invalid confirmation toward `num_confirmations` forever.

Concretely, with 4 members and `num_confirmations = 3`:
1. Key `A` creates+confirms request `R` (`confirmations = {A}`).
2. Key `B` confirms `R` (`confirmations = {A, B}`, count = 2, below threshold, so `R` stays pending).
3. Members `A, C, D` separately pass a `DeleteKey{public_key: B}` request (3-of-4), removing `B` from the account. Only requests *created* by `B` (and its `num_requests_pk` entry) are purged — `R`'s stored confirmation set `{A, B}` is untouched.
4. Key `C` now confirms `R`: `confirmations = {A, B, C}`, count = 3 ≥ `num_confirmations` (3), and `R` executes.

`R` is executed as if it had 3-of-4 (or 3-of-current-membership) approval, but only two currently-authorized keys (`A` and `C`) actually approved it; `B`'s approval is stale because `B` no longer holds a key on the account. This breaks the equality `confirmations counted == confirmations from live members` and effectively executes a multisig request below its intended live-member threshold.

### Impact Explanation
This falls under the explicitly listed Critical impact: "a multisig request executed below threshold." A `Transfer`, `AddKey`/`DeleteKey`, `FunctionCall`, or `DeployContract` request (including draining funds or replacing the entire keyset) can be pushed through with fewer live approvals than the configured `num_confirmations`, undermining the entire K-of-N security guarantee the contract is supposed to provide.

### Likelihood Explanation
The precondition is a normal operational event — rotating out a compromised or departing member's key via the documented `DeleteKey` action — combined with any request that was partially confirmed before the rotation. No key theft or privileged bypass is required beyond the multisig's own, already-authorized `DeleteKey` governance action; the remaining members do not need to collude maliciously, they simply need to not realize that old, partially-confirmed requests retain the departed member's confirmation. Given that key rotation is an expected lifecycle event for any long-lived multisig account, and pending unconfirmed requests are common (the contract even allows up to 12 concurrent active requests per key), this is readily reachable.

### Recommendation
When executing `DeleteKey` (or the equivalent `DeleteMember` in `multisig2`), iterate over all entries in `self.confirmations` (not just requests created by the deleted key) and remove the deleted key/member from every confirmation set. Alternatively, validate at `confirm()`-execution time that every public key in the stored confirmation set for a request still corresponds to a currently valid access key/member before counting it toward `num_confirmations`.

### Proof of Concept
```rust
// multisig/src/lib.rs — illustrative sequence (pseudocode of on-chain calls)

// members: A, B, C, D ; num_confirmations = 3
c.add_request_and_confirm(transfer_request); // request R, confirmations = {A}
// as B:
c.confirm(r_id); // confirmations = {A, B}, count = 2 < 3 -> pending

// A, C, D pass a DeleteKey(B) request (3-of-4 confirmed by A, C, D)
c.add_request_and_confirm(MultiSigRequest {
    receiver_id: current_account_id(),
    actions: vec![MultiSigRequestAction::DeleteKey { public_key: B_key }],
});
// -> only requests created by B are purged; R's confirmations {A, B} remain untouched
// -> B's access key is now removed from the account

// as C:
c.confirm(r_id); // confirmations = {A, B, C}, count = 3 >= num_confirmations(3)
// R executes with only 2 currently-valid approvals (A, C) plus 1 stale one (B)
```
Inspecting `execute_request`'s `DeleteKey` branch confirms it filters `self.requests` by `r.signer_pk == pk` only and never touches `self.confirmations` entries where `pk` appears merely as a confirmer: [2](#0-1) , while `confirm()` blindly trusts the stored `HashSet<PublicKey>` size: [3](#0-2) .

Note: I was not able to fully verify within the remaining time whether `multisig2/src/lib.rs` (which uses `AddMember`/`DeleteMember` instead of raw access keys) has an equivalent gap in its `DeleteMember` handling; its `confirm()` logic is structurally identical ( [4](#0-3) ), so the same class of issue should be checked there as well.

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

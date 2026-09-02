### Title
Multisig executes requests using stale confirmations from removed signer keys, allowing execution below the live-member threshold - (File: multisig/src/lib.rs)

### Summary
`MultiSigContract::confirm` counts confirmations from an `UnorderedMap<RequestId, HashSet<PublicKey>>` that is never pruned when a public key is revoked via a `DeleteKey` request. `execute_request`'s `DeleteKey` handling only cleans up **requests created by** the removed key (`r.signer_pk == pk`) and the removed key's own `num_requests_pk` counter; it never scans `self.confirmations` to strip that key's prior confirmations from **other** pending requests. As a result, a request can reach `num_confirmations` and execute even though one (or more) of the counted confirmations belongs to a key that is no longer part of the multisig's live signer set.

### Finding Description
The binding that should hold is:

`confirmations from currently-authorized signers on request R >= num_confirmations`

but the contract actually enforces:

`|confirmations HashSet(R)| >= num_confirmations`

regardless of whether every public key in that set is still a valid signer.

- `confirm()` only checks the caller hasn't confirmed this request yet and then compares the *stored* confirmation count against the threshold: [1](#0-0) 
- `DeleteKey` execution removes only the deleted key's own **created** requests and its `num_requests_pk` entry, then issues an async `promise.delete_key(pk)`. It does not touch `self.confirmations` entries for requests created by *other* signers that this key previously confirmed: [2](#0-1) 

Because of this gap, once a key `D` has confirmed some pending request `R` (created by another signer, e.g. `A`), and is later removed from the multisig via a legitimate `DeleteKey` action, `D`'s confirmation on `R` is never invalidated. If the remaining valid signers subsequently push `R`'s confirmation count up to `num_confirmations` (counting `D`'s stale confirmation as one of the votes), `execute_request` runs even though only `num_confirmations - 1` currently-authorized keys actually approved it.

### Impact Explanation
This breaks the multisig's core custody guarantee: a request (e.g. `Transfer`, `AddKey` granting full access, `FunctionCall`) can be executed with fewer live, currently-authorized confirmations than the configured threshold requires. This directly matches the Critical impact category "a multisig request executed below threshold," since funds or control of the account can move without the intended number of currently-trusted keys agreeing.

### Likelihood Explanation
This requires no privileged access beyond being a legitimate multisig signer at some point: a key that confirms a pending request and is later revoked (e.g., a departing team member, or a compromised key that is proactively removed after having already confirmed something) leaves a residual, uncounted-for confirmation that silently degrades the effective threshold. This is a realistic operational sequence (revoke-then-continue-confirming) rather than a contrived edge case, since `delete_request`/`DeleteKey` flows are normal multisig hygiene operations.

### Recommendation
When executing `DeleteKey`, iterate over `self.confirmations` for all pending requests (not just those created by `pk`) and remove `pk` from each request's confirmation `HashSet`. Alternatively, validate at `confirm()`/`execute_request()` time that every public key in the stored confirmation set is still a currently valid access key on the account before counting it toward the threshold.

### Proof of Concept
1. Deploy multisig with `num_confirmations = 3` and signer keys `A`, `B`, `C`, `D`.
2. `A` calls `add_request(R)` for a sensitive action (e.g. `Transfer` or `AddKey`).
3. `D` calls `confirm(R)` → confirmations(R) = `{D}` (1/3).
4. Separately, `A`, `B`, `C` execute a `DeleteKey { public_key: D }` request through the normal multisig flow, revoking `D` as a signer (`execute_request` only prunes `D`'s own requests and `num_requests_pk`, per [2](#0-1) ; `R`'s confirmation set is untouched).
5. `B` calls `confirm(R)` → confirmations(R) = `{D, B}`, size 2, `2 + 1 >= 3` → `execute_request(R)` fires per [3](#0-2) .
6. `R` executes using only `B`'s (and the original creator `A`'s) live approval plus the stale, now-invalid confirmation from revoked key `D` — one fewer live-signer confirmation than the configured threshold of 3.

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

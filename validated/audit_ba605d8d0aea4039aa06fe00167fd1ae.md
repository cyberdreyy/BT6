Confirmed the mechanism. Here's the analog finding.

### Title
Multisig confirmations from deleted keys remain counted toward the K-of-N threshold, allowing requests to execute with fewer live confirmations - ([File: multisig/src/lib.rs])

### Summary
This report describes Meta protocol's mUSD bug where a stale, no-longer-backed claim (`borrowed[user]`) keeps counting toward a global entitlement total after the position that justified it (`deposited[user]`) is gone. The structural analog in `multisig/src/lib.rs` is that a stale confirmation recorded by a public key that has since been removed from the multisig keeps counting toward `num_confirmations` after the key is deleted, because `DeleteKey` only purges confirmations for requests *originated* by that key, not confirmations that key *cast* on other still-pending requests.

### Finding Description
`MultiSigContract` stores, per request, a `HashSet<PublicKey>` of confirming keys in `self.confirmations` [1](#0-0) . `confirm()` treats `confirmations.len() + 1 >= self.num_confirmations` as the trigger to execute the request [2](#0-1) , i.e. it counts *recorded* confirming keys as a proxy for *currently live* signer keys, with the implicit invariant that every key in that set is still an authorized member.

When a key is removed via `MultiSigRequestAction::DeleteKey`, the cleanup only removes confirmations for requests where `r.signer_pk == pk` — i.e. requests that key *added* — and only removes `num_requests_pk` bookkeeping for that key: [3](#0-2) 

It never scans `self.confirmations` for *other* pending requests where the deleted key appears only as a confirmer (added via `confirm()`, not as the original requester). Those entries are left untouched, so the deleted key's public key remains a member of those requests' confirmation sets forever.

This breaks the intended equality `confirmations.len() == number of currently-authorized keys that approved`. After a key is deleted, `confirmations.len()` for any request it had previously confirmed still includes that dead key, so the threshold check in `confirm()` requires one fewer *live* signature than `num_confirmations` actually specifies.

### Impact Explanation
This is exactly the "a multisig request executed below threshold" case: a K-of-N multisig can be made to execute a request with only K-1 (or fewer, if multiple keys were later removed) live, currently-valid signatures. Any member combined with a stale ghost confirmation from a removed key can push a `Transfer`, `AddKey`, `FunctionCall`, etc. request through without the number of live approvals the contract was configured to require — undermining the core security guarantee of the multisig (arbitrary transfers/actions authorized below the configured threshold).

### Likelihood Explanation
No privileged or out-of-scope action is required beyond normal multisig lifecycle usage that the contract itself exposes: (1) a key confirms some request R1 without it reaching threshold yet, (2) that key is later removed via a normal `DeleteKey` request (an ordinary, expected multisig operation for key rotation/compromise response), (3) any remaining live members supply the remaining confirmations on R1. Because R1's confirmation set is never re-validated against currently live keys, R1 executes with fewer live approvals than `num_confirmations`. Key rotation/removal is a normal, expected occurrence in a multisig's lifetime, not an edge case, so the precondition is highly likely to occur in practice.

### Recommendation
When a key (or member, in `multisig2`) is deleted, scan all pending requests' confirmation sets and remove the deleted key/member from every one of them, not only from requests it originated — mirroring the "clear the bad debt when the position disappears" fix used by the Meta team. Alternatively, revalidate at `confirm()`-time that every key present in a request's confirmation set is still a currently valid key before counting it toward `num_confirmations`.

### Proof of Concept
1. Deploy multisig with keys `A`, `B`, `C`, `num_confirmations = 3`.
2. `A` calls `add_request_and_confirm(R1)` (some `Transfer` request) → `confirmations[R1] = {A}`.
3. Owner submits and confirms a separate `DeleteKey{public_key: A}` request through the normal K-of-3 process, removing `A` as a valid key. `execute_request`'s `DeleteKey` branch only purges confirmations for requests where `signer_pk == A`; R1 was added by `A` as signer too in this simple case — to make the bug maximally clear, have `B` be the one who calls `add_request` for R1 and `A` merely `confirm()`s it, so `r.signer_pk == B != A`, and R1's confirmations are untouched by the `DeleteKey` cleanup.
4. `confirmations[R1]` still equals `{A}` even though `A`'s key was just deleted from the account.
5. `B` calls `confirm(R1)` → `confirmations.len() (1) + 1 = 2 < 3`, so it's added: `confirmations[R1] = {A, B}`.
6. `C` calls `confirm(R1)` → `confirmations.len() (2) + 1 = 3 >= 3` → `execute_request(R1)` runs.
7. R1 executed with only 2 live keys (`B`, `C`) confirming, even though the contract requires 3 live confirmations — the dead key `A`'s stale confirmation was counted as the third. [2](#0-1) [3](#0-2)

### Citations

**File:** multisig/src/lib.rs (L79-89)
```rust
#[near_bindgen]
#[derive(BorshDeserialize, BorshSerialize)]
pub struct MultiSigContract {
    num_confirmations: u32,
    request_nonce: RequestId,
    requests: UnorderedMap<RequestId, MultiSigRequestWithSigner>,
    confirmations: UnorderedMap<RequestId, HashSet<PublicKey>>,
    num_requests_pk: UnorderedMap<PublicKey, u32>,
    // per key
    active_requests_limit: u32,
}
```

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

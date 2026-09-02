I have enough evidence to write up the finding on the confirmation/member-removal binding break in the multisig contracts.

### Title
Stale confirmations from removed multisig keys/members are still counted toward the confirmation threshold, allowing requests to execute below the intended `num_confirmations` threshold - (File: `multisig/src/lib.rs`, `multisig2/src/lib.rs`)

### Summary
The custody binding a multisig is supposed to guarantee is: *confirmations counted == confirmations from currently live members*. In both `multisig` and `multisig2`, when a key/member is removed via `DeleteKey`/`DeleteMember`, the contract only purges **requests originated by** that key/member, and its own `num_requests_pk` bookkeeping entry. It never scans `self.confirmations` to strip that key/member's stale confirmation from *other* pending requests it had already confirmed. Those stale confirmations remain counted in the `confirmations.len() as u32 + 1 >= self.num_confirmations` check in `confirm()`, so a request can execute with fewer live, currently-authorized confirmers than `num_confirmations` actually requires.

### Finding Description
In `multisig/src/lib.rs`, `execute_request` handles `MultiSigRequestAction::DeleteKey`: [1](#0-0) 
It filters `self.requests` for entries where `r.signer_pk == pk` (i.e., requests *created* by the deleted key) and removes those requests plus their confirmation sets, and removes the `num_requests_pk` entry for that key. It does **not** iterate `self.confirmations` to remove `pk` from confirmation sets of requests created by *other* keys that `pk` had already confirmed.

The same shape of bug exists in `multisig2/src/lib.rs`'s `delete_member`: [2](#0-1) 
It filters `self.requests` by `r.member == member` (requests originated by the removed member) and clears those; it never scans confirmations of requests originated by other members.

Both variants' `confirm()` then blindly trusts the stored confirmation set size: [3](#0-2) [4](#0-3) 

So the equality the system is meant to preserve — `confirmations recorded for request R == confirmations by keys/members that are members at the time of execution` — is broken: a request can carry a confirmation from a key/member that has since been deleted, and that stale confirmation is still added to the count that triggers `execute_request`.

### Impact Explanation
Concrete scenario (multisig v1, `num_confirmations = 2`, keys A, B, C):
1. Key C creates request R (transfer of funds) via `add_request`. `confirmations[R] = {}`.
2. Key A confirms R: `confirmations[R] = {A}`. Not yet ≥ 2, so R stays pending.
3. Separately (unrelated legitimate multisig action), the multisig removes key A (`DeleteKey{A}` executed via a separate, properly-confirmed request). `execute_request` for `DeleteKey{A}` only deletes requests *authored* by A and `num_requests_pk[A]`; request R (authored by C, confirmed by A) is untouched, so `confirmations[R]` still equals `{A}`.
4. Key B confirms R: `confirmations[R].len() + 1 = 2 >= num_confirmations(2)` → R executes.

R executed with only B being a currently-authorized signer confirming it — A's confirmation was stale (A is no longer a member) — i.e., the request was authorized by only 1 live signer while the contract's policy required 2. This directly matches "a multisig request executed below threshold," letting NEAR be transferred out of the account (or a key/contract-code change be applied) without the intended number of live approvers, which an attacker who controls or colludes with one still-pending confirmer plus the removed key's earlier confirmation can exploit deliberately (e.g., have a key it controls confirm a malicious request, then get that key legitimately rotated/removed, then get one more real signer to confirm, executing with effectively 1 live approval instead of 2).

### Likelihood Explanation
This requires no special privilege beyond being one of the `N` multisig signers (a normal, expected participant in "add key, confirm, later remove key" workflows that are explicitly documented as supported operations — key rotation/removal is a normal, non-privileged-to-attacker-model operation for any signer who is a member). Because key rotation is a routine multisig maintenance operation, and nothing in the confirm/delete-key code paths cross-checks confirmations against currently-live members, this can be triggered unintentionally in normal operation, and can be engineered deliberately by a signer who wants to reduce the effective confirmation threshold for a specific pending request.

### Recommendation
When executing `DeleteKey` (`multisig`) or `DeleteMember` (`multisig2`), iterate over **all** pending requests' confirmation sets (not just requests authored by the removed key/member) and remove the deleted key/member's entry from each. Alternatively, when counting confirmations in `confirm()`, filter `confirmations` against the current live key/member set (e.g., recompute `confirmations.iter().filter(|pk| is_current_member(pk)).count()`) before comparing to `num_confirmations`, so stale confirmations from removed signers never count toward execution.

### Proof of Concept
Rust unit test sketch for `multisig/src/lib.rs` (analogous to existing `add_key_delete_key_storage_cleared` test):
```rust
#[test]
fn test_stale_confirmation_survives_key_deletion() {
    // three keys: A, B, C, num_confirmations = 2
    let mut c = MultiSigContract::new(2);

    // C creates request R to transfer funds (receiver_id = alice, i.e. this account)
    testing_env!(context_with_key(pk_c(), 1_000));
    let transfer_request = MultiSigRequest {
        receiver_id: alice(),
        actions: vec![MultiSigRequestAction::Transfer { amount: 500.into() }],
    };
    let r = c.add_request(transfer_request);

    // A confirms R (1 confirmation so far)
    testing_env!(context_with_key(pk_a(), 1_000));
    c.confirm(r);
    assert_eq!(c.get_confirmations(r).len(), 1);

    // Multisig legitimately removes key A via a separate DeleteKey request/confirm flow
    testing_env!(context_with_key(pk_c(), 1_000));
    let delete_a_request = MultiSigRequest {
        receiver_id: alice(),
        actions: vec![MultiSigRequestAction::DeleteKey { public_key: pk_a() }],
    };
    let del_id = c.add_request(delete_a_request);
    testing_env!(context_with_key(pk_b(), 1_000));
    c.confirm(del_id); // executes DeleteKey{A} (assume 1-of-1 for this example, or adjust threshold)

    // R's confirmation set still contains A, even though A is no longer a valid signer
    assert_eq!(c.get_confirmations(r).len(), 1); // stale confirmation from deleted key A still present

    // B confirms R: count becomes 2 (>= num_confirmations), so R executes
    // even though only B is currently a live, authorized confirmer.
    testing_env!(context_with_key(pk_b(), 1_000));
    c.confirm(r); // executes transfer with effectively 1 live approver instead of 2
}
```

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

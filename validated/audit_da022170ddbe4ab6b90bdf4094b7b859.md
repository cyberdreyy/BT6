## Title
Stale confirmations from deleted multisig members remain counted toward `num_confirmations`, allowing requests to execute below the intended live-member threshold - (File: `multisig2/src/lib.rs`, also `multisig/src/lib.rs`)

## Summary
`MultiSigContract::confirm` (both `multisig` and `multisig2`) authorizes execution purely by comparing the *stored* size of a request's `confirmations` set to `num_confirmations`. When a member is removed via `DeleteMember`/`DeleteKey`, the contract only purges confirmations/requests that member *originated*, not confirmations that member previously cast on requests originated by other members. Those stale confirmations remain in the `confirmations` set and continue to count toward the threshold, so a request can execute with fewer *live* member approvals than `num_confirmations` requires.

## Finding Description
`confirm()` decides whether to execute a request solely from the recorded confirmation count: [1](#0-0) 

Deleting a member is handled by `delete_member`, which removes only requests *originated* by that member (`r.member == member`) and the member's own `num_requests_pk` entry, then removes the member from `self.members`: [2](#0-1) 

Nowhere does this routine (or `DeleteKey` in `multisig/src/lib.rs`) scan the `confirmations: LookupMap<RequestId, HashSet<String>>` map for *other* requests that the removed member had already confirmed: [3](#0-2) 

The equivalent v1 logic exhibits the same gap — `DeleteKey` only removes requests where `r.signer_pk == pk` (the request's own signer), leaving that key's confirmations on other requests intact: [4](#0-3) 

This is the same class of bug as the reported liquidation-threshold issue: a protocol enforces a safety threshold (`collateralizationRate` for both LTV and liquidation start / `num_confirmations` for authorization) using a value that has silently drifted out of sync with the real state it is supposed to reflect (borrower's true health / the actual set of live, trusted members). Here the binding that should hold is:

```
count(confirmations for request R) == count(live members who currently intend to approve R)
```

Once a confirming member is deleted, the left side no longer reflects the right side — a phantom confirmation from a removed member survives and is treated as equivalent to a real, live approval.

## Impact Explanation
This breaks a Critical-tier custody guarantee: "a multisig request executed below threshold." Concretely:

1. Members are `A`, `B`, `C`, `num_confirmations = 2`.
2. `B` creates request `X` (e.g. `Transfer`). `A` confirms `X` (1/2 confirmations recorded).
3. Separately, the group later passes a `DeleteMember { member: A }` request (e.g. because `A`'s key is believed compromised, or `A` is leaving). `delete_member` removes `A` from `members` and removes only requests *A originated*; `X` (originated by `B`) and `A`'s confirmation on it are untouched.
4. Live membership is now `{B, C}`. `C` confirms `X`. `confirmations.len() == 2 >= num_confirmations (2)`, so `X` executes — even though only one currently-live member (`C`) actually approved it after `A`'s removal. The transfer/`FunctionCall`/`AddKey` action in `X` executes on the multisig's authority despite not having genuine 2-of-2 (or 2-of-N) live consensus.

Because multisig accounts typically custody NEAR/wNEAR and control access keys, this allows moving funds or granting access with fewer real approvals than the configured threshold — a direct violation of the multisig's core security invariant.

## Likelihood Explanation
The path requires no external/unprivileged attacker action beyond normal, expected multisig operation: any request that has partial confirmations at the time a confirmer is removed will retain that stale confirmation. Removing a member for entirely legitimate reasons (key rotation, personnel change, suspected compromise) is a routine multisig lifecycle operation, so the divergence is easy to trigger unintentionally, and a departing/compromised member's earlier confirmation can be leveraged intentionally by the remaining signers (or by the removed member colluding beforehand) to push a request through with less real consensus than configured.

## Recommendation
When executing `DeleteMember`/`DeleteKey`, iterate all active `requests`/`confirmations` entries and remove the deleted member's confirmation from every request they had confirmed (not just requests they originated), analogous to how `assert_valid_request`/`remove_request` already scrub per-originator state. Alternatively, validate membership of every entry in a request's `confirmations` set live at `confirm()`-time (i.e., only count confirmations from accounts/keys still present in `self.members`) before comparing against `num_confirmations`.

## Proof of Concept
Given `MultiSigContract::new(members: [A, B, C], num_confirmations: 2)`:
1. As `B`: `add_request(X)` → `request_id = 0`.
2. As `A`: `confirm(0)` → `confirmations[0] = {A}` (size 1 < 2, not yet executed).
3. As `B`/`C` (reaching threshold on a separate request): `add_request_and_confirm(DeleteMember{member: A})`, then `confirm` from `C` → executes `delete_member`, which removes `A` from `members`, but only deletes requests where `r.member == A`; request `0` (owned by `B`) and `confirmations[0] = {A}` remain untouched, per: [5](#0-4) 
4. As `C`: `confirm(0)` → `confirmations[0].len() + 1 == 2 >= num_confirmations`, so `execute_request(X)` runs, per: [6](#0-5) 
5. `X` (e.g. a `Transfer`) executes despite only `C` being a genuinely live confirming member post-removal — one fewer live confirmation than `num_confirmations` intends.

Note: I was not able to execute this scenario against a live test harness (no filesystem/terminal access in this mode); the analysis is based on static reading of `multisig2/src/lib.rs` (and the analogous `multisig/src/lib.rs`) logic for `confirm`, `delete_member`/`DeleteKey`, and the `confirmations` map structure shown above.

### Citations

**File:** multisig2/src/lib.rs (L126-128)
```rust
    requests: UnorderedMap<RequestId, MultiSigRequestWithSigner>,
    /// All confirmations for active requests.
    confirmations: LookupMap<RequestId, HashSet<String>>,
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

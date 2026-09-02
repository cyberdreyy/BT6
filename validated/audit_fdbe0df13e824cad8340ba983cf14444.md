## Title
Stale confirmations from removed multisig members/keys remain counted toward the confirmation threshold, allowing a request to execute below the effective live-member threshold - (File: `multisig2/src/lib.rs`, `multisig/src/lib.rs`)

### Summary
The Gondi `repayLoan` bug is caused by a stale reference (`_loans[loanId]`) that is silently invalidated/rewritten by an unrelated privileged-looking action (`mergeTranches`), so a check that used to be valid becomes invalid mid-flight. The NEAR multisig contracts have the mirror-image defect: a stale reference (a removed member's/key's confirmation) that *should* become invalid when membership changes continues to be counted as valid, letting `confirm()` cross the approval threshold using fewer live approvers than `num_confirmations` requires.

### Finding Description
In `multisig2/src/lib.rs`, `confirm()` only checks that the calling member hasn't already confirmed, then compares the raw size of the stored `confirmations` set to `num_confirmations`: [1](#0-0) 

Confirmations are added by member identity string (`member.to_string()`) with no re-validation that every entry already in the set still corresponds to a current member at execution time.

When a member is removed via `DeleteMember`, the cleanup logic only removes **requests that member created** (`r.member == member`), not confirmations that member previously cast on requests created by someone else: [2](#0-1) 

The same gap exists in the original `multisig` contract: `DeleteKey` only purges requests where `r.signer_pk == pk` (the request's original creator), leaving that key's confirmations on other pending requests untouched: [3](#0-2) 

The intended binding is: `confirmations_counted(request_id)` should always equal `|{ live members who explicitly approved request_id }|`. Because removal only sweeps requests by *creator* identity, not by *confirmer* identity, a removed member's prior confirmation persists in `self.confirmations` and is still summed against `num_confirmations` in `confirm()`.

### Impact Explanation
This directly matches the in-scope Critical impact "a multisig request executed below threshold." A pending request can be executed (funds transferred, keys/members added, contract deployed, etc.) with only `num_confirmations - 1` (or fewer) approvals from members who are actually current, because one of the counted approvals belongs to a member/key that has since been removed — e.g., after being detected as compromised or malicious. The multisig's core security guarantee (k-of-n live signers) is broken.

### Likelihood Explanation
This requires only ordinary multisig usage, no privileged access beyond being (or having been) one of the `n` members — exactly the kind of unprivileged-member scenario the report's front-running analog describes: a member confirms a request, is later removed (e.g., a compromised key is revoked), and the stale confirmation is never cleaned. It is especially likely in the realistic case of key rotation/compromise response, where a team removes a member precisely because they no longer trust that signer — yet that signer's earlier approvals keep counting.

### Recommendation
- When executing `DeleteMember`/`DeleteKey`, also strip the removed member's/key's entry from `confirmations` on *every* outstanding request, not only requests they created; or
- Have `confirm()` (and the threshold check) re-validate, at counting time, that every entry in the stored `confirmations` set still corresponds to a current `self.members` entry, ignoring stale ones.

### Proof of Concept
1. Deploy `multisig2` with members `{A, B, C}` and `num_confirmations = 2`.
2. `A` calls `add_request(request)` for a sensitive action (e.g., `Transfer`) — creates request with `member = A`, empty confirmations.
3. `B` calls `confirm(request_id)` — `confirmations = {B}`, `1 < 2`, so it's just stored, no execution.
4. Team detects `B`'s key is compromised and removes `B` via a separate `DeleteMember { member: B }` request, approved by `A` and `C` (2 confirmations, matching threshold) — see `delete_member` at `multisig2/src/lib.rs:356-379`. This only deletes requests where `r.member == B` (requests *B* created); the transfer request from step 2 is untouched and still has `confirmations = {B}`.
5. `C` (a legitimate remaining member, unaware the stale confirmation exists) calls `confirm(request_id)` on the original transfer request. `confirmations.len() + 1 == 2 >= num_confirmations`, so `execute_request` runs — see `confirm()` at `multisig2/src/lib.rs:294-315`.
6. The transfer executes with only one truly live approval (`C`); `B`'s counted approval came from a member already removed from `self.members`, i.e., the request executed below the effective 2-of-live-members threshold.

### Citations

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

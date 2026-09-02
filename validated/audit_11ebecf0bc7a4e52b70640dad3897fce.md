### Title
Stale confirmations from removed multisig members remain valid, allowing requests to execute below the live-member quorum threshold - (File: multisig2/src/lib.rs)

### Summary
`MultiSigContract::delete_member` only purges pending requests that were *authored* by the removed member, but never purges the removed member's confirmation votes recorded against requests authored by other members. `MultiSigContract::confirm` counts the size of the persisted `confirmations` set toward `num_confirmations` without checking whether each entry still corresponds to a current member. As a result, a request can execute with fewer *live* member approvals than `num_confirmations` actually requires, because a "ghost" confirmation from a since-removed member is still tallied.

### Finding Description
The multisig's security guarantee is that `num_confirmations` distinct **current** members must approve a request (`MultiSigContract::confirm`, `multisig2/src/lib.rs#L294-L315`): [1](#0-0) 

Membership removal is implemented in `delete_member`: [2](#0-1) 

`delete_member` only removes requests where `r.member == member`, i.e. requests that the removed member *originally added* (via `add_request`). It never inspects the `confirmations: LookupMap<RequestId, HashSet<String>>` map for requests added by *other* members that the removed member had already confirmed. Those stale confirmation strings are never invalidated or cleaned.

The binding that should hold is:
`confirmations recorded for request R that come from members currently in self.members == confirmations counted toward num_confirmations for R`

Once a confirming member is removed, this equality breaks: the `confirmations` set for R still contains the removed member's identity string, and `confirm`'s check `confirmations.len() as u32 + 1 >= self.num_confirmations` (`multisig2/src/lib.rs#L304`) counts it as a valid vote even though that account/key is no longer a member and can no longer independently confirm anything.

### Impact Explanation
This is a Critical-impact issue per the "a multisig request executed below threshold" criterion: a request that is supposed to require `num_confirmations` approvals from currently-authorized members can be pushed through with fewer live approvals, because one or more of the counted confirmations belong to accounts/keys that have already been removed from the multisig. An attacker who can secure approvals from `num_confirmations - 1` (or fewer) *current* members, in combination with a stale confirmation left behind by a member removed after confirming but before the request finished collecting signatures, can execute arbitrary actions on the multisig's behalf — including `Transfer`, `FunctionCall`, `AddKey`/`DeployContract` — draining or reassigning control of the account's NEAR balance.

### Likelihood Explanation
This does not require any privileged bug or protocol violation — it only requires the ordinary, expected lifecycle of a multisig: a member confirms a pending request, and is later removed (e.g., key rotation, offboarding, or a legitimate governance action unrelated to that specific request) before the request accumulates the remaining confirmations. This is a realistic and even routine operational sequence for any long-lived multisig with more than a couple of pending requests, making the likelihood moderate-to-high in practice, not merely theoretical.

### Recommendation
When removing a member in `delete_member`, iterate over all pending requests' `confirmations` sets and strip the removed member's identity string from each of them (not only requests the member originally authored). Alternatively, validate at `confirm`-time / execution-time that every entry in the `confirmations` set still belongs to `self.members` before counting it toward `num_confirmations`.

### Proof of Concept
1. Initialize multisig with members `[A, B, C, D]` and `num_confirmations = 3`.
2. `A` calls `add_request` with a `Transfer` action draining the account (request `id=0`), recorded with `member = A`.
3. `C` calls `confirm(0)` → `confirmations = {A? no, only confirmers stored} = {C}` (size 1, `1+1 < 3`, not yet executed).
4. Multisig legitimately removes `C` via a separate `DeleteMember{member: C}` request confirmed by `A`, `B`, `D` (unrelated to request `0`). `delete_member` only clears requests where `r.member == C`; request `0` was authored by `A`, so it and its `confirmations = {C}` survive untouched.
5. `B` calls `confirm(0)` → `confirmations.len() (1) + 1 = 2 < 3`, stored as `{C, B}`.
6. `D` calls `confirm(0)` → `confirmations.len() (2) + 1 = 3 >= 3` → `execute_request` runs the `Transfer`, even though only `A`(implicit author, not a confirmer), `B`, and `D` are current members who actually acted, and `C`'s vote — from an account no longer part of the multisig — was counted as one of the three required approvals. [2](#0-1) [1](#0-0)

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

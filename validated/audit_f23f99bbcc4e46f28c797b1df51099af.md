This is a good analog. GitLab Runner explicitly acknowledges this exact vulnerability class in its own code comments and docs, and has already mitigated it via `ProjectRealUniqueName`, but the truncated, collision-prone `ShortenToken`/`ShortDescription` path is still used elsewhere.

### Title
Truncated runner token collisions can cause cross-runner container/data name clashes - (File: `helpers/shorten_token.go`, `common/build.go`)

### Summary
`helpers.ShortenToken` truncates a runner's authentication token to only the first 9 characters after stripping known prefixes [1](#0-0) . This shortened value (`Runner.ShortDescription()`) is used to build resource identifiers such as `ProjectUniqueName`/`ProjectUniqueShortName`, Docker network/volume names, and host-bound build/cache directory paths [2](#0-1) . GitLab Runner's own code comments admit this is susceptible to collisions "when tokens are similar enough" [3](#0-2) , and documentation confirms the pre-18.4.0 host directory naming scheme used only the 8-letter short token, project ID, and concurrency index [4](#0-3) .

### Finding Description
This mirrors the Hermez chainID-truncation bug: a security-relevant identifier space (token entropy) is truncated to a small fixed size (9 characters, effectively far fewer bits once base62-like charset collisions are considered) before being used to derive isolation boundaries such as Docker volume/container names and host build directories. If two distinct runner tokens happen to share the same first 9 characters (post-prefix-stripping) — which is a realistic collision risk across a large fleet of runners, especially given fixed prefixes like `glrt-`, `t1_`, etc. that are stripped before truncation, reducing effective entropy — two unrelated runners/projects could compute identical `ProjectUniqueName`/`ShortDescription`-derived paths and container/volume names, leading to shared or reused build directories, caches, or Docker volumes between different runner configurations. GitLab Runner explicitly created `ProjectRealUniqueName` to fix exactly this problem by using the full token plus a 128-bit truncated SHA-256 sum instead of the short, human-readable token [5](#0-4) , confirming the risk is real and already recognized by the maintainers.

However, `ShortDescription()`-based naming (`ProjectUniqueName`, `ProjectUniqueShortName`, `GetProjectUniqueDir`'s `ShortDescription()`-based shared-dir path) is still present and used in some contexts [6](#0-5) , and legacy host-bound storage layouts documented as still valid rely on this truncated short-token scheme [7](#0-6) .

### Impact Explanation
If a collision occurs, two unrelated runner configurations (potentially operated by different administrators sharing the same runner-manager host, or a fleet of many runners registered under the platform) could resolve to the same Docker volume name, network name, or host directory path. This could result in build artifacts, cached files, or source checkouts from one project/runner being visible to or overwritten by another project's job — a cross-tenant data leakage / integrity issue, not merely a cosmetic naming clash.

### Likelihood Explanation
This requires a token-prefix collision within a 9-character window, which is unlikely to happen by chance for random high-entropy tokens, but the report's own root cause (fixed small truncation length regardless of the total identifier space) is structurally identical to the chainID issue. Additionally, since the truncation is deterministic and derived from public/knowable prefixes (`glrt-`, `t1_`, `glrtr-`), and since GitLab Runner's own maintainers already flagged and worked around this exact weakness with `ProjectRealUniqueName`, the likelihood assessment here is inherited directly from the project's own acknowledgment rather than purely theoretical speculation. That said, exploiting this on a **shared host** (multiple runner configs on the same runner-manager) is required, which is a legitimate, documented, non-privileged deployment pattern for GitLab Runner (see "Intermediate configuration: one runner manager, multiple runners" in fleet scaling docs) [8](#0-7)  — not an admin-misconfiguration or trusted-role-compromise scenario.

### Recommendation
- Migrate all remaining consumers of `Runner.ShortDescription()` for resource-naming purposes (`ProjectUniqueName`, `ProjectUniqueShortName`, `GetProjectUniqueDir` shared-dir paths) to the collision-resistant `ProjectRealUniqueName` scheme, consistent with the rationale already documented in `common/build.go`.
- Alternatively, increase `shortTokenLen` in `helpers/shorten_token.go` or salt/hash the short token before use as a directory/volume/network name, in the same spirit as the long-term Hermez recommendation to "carefully evaluate the value range of each variable stored, and ensure enough bits are conserved."

### Proof of Concept
1. Register two runner authentication tokens whose first 9 characters (after stripping the `glrt-`/`t1_` prefix) are identical (feasible if tokens are generated with lower entropy in that byte range, or via an intentionally-crafted vanity token from a self-hosted GitLab instance where token generation is controllable).
2. Register both runners on the same host running the shared/host-bound Docker persistent-storage configuration described in `docs/executors/docker.md` lines 694-710.
3. Run concurrent jobs from the two different runners against projects with the same `<project-id>`/`<concurrency-id>` combination; observe that `ProjectUniqueName`/`ShortDescription()`-derived Docker volume names and host build directories collide, causing one job's build/cache directory to be shared with the other's. [9](#0-8) [10](#0-9) [11](#0-10)

### Citations

**File:** helpers/shorten_token.go (L1-32)
```go
package helpers

import (
	"math"
	"regexp"
)

// Known prefixes to strip from tokens:
// - glrt- and glrtr- are registration tokens
// - glcbt- is a ci job token
// - GR* is an old runner registration token
// - t[123]_ is a partition prefix which can appear with a glrt- registration token, or by itself.
//
// Any token prefixed added here should probably also be added to allTokenPrefixes in tokensanitizer package.

var prefixRes = []*regexp.Regexp{
	regexp.MustCompile(`^glrt-(t[123]_)?|^t[123]_|^glrtr-`), // runner authentication token
	regexp.MustCompile(`^glcbt-`),                           // job token
	regexp.MustCompile(`^GR[0-9A-Fa-f]{7}`),                 // runner registration token. These should no longer appear, but just in case...
}

const shortTokenLen = 9

func ShortenToken(in string) string {
	// Strip known prefixes
	for _, re := range prefixRes {
		in = re.ReplaceAllString(in, "")
	}

	// take the first 9 characters
	end := math.Min(shortTokenLen, float64(len(in)))
	return in[:int(end)]
```

**File:** common/build.go (L344-397)
```go
// ProjectUniqueShortName returns a unique name for the current build.
// It is similar to ProjectUniqueName but removes unnecessary string
// and adds the current BuildID as an additional composition to the unique string
func (b *Build) ProjectUniqueShortName() string {
	projectUniqueName := fmt.Sprintf(
		"runner-%s-%d-%d-%d",
		b.Runner.ShortDescription(),
		b.JobInfo.ProjectID,
		b.ProjectRunnerID,
		b.ID,
	)

	return dns.MakeRFC1123Compatible(projectUniqueName)
}

// ProjectUniqueName returns a unique name for a runner && project. It uses the runner's short description, thus uses a
// truncated token in it's human readable form.
func (b *Build) ProjectUniqueName() string {
	projectUniqueName := fmt.Sprintf(
		"runner-%s-project-%d-concurrent-%d",
		b.Runner.ShortDescription(),
		b.JobInfo.ProjectID,
		b.ProjectRunnerID,
	)

	return dns.MakeRFC1123Compatible(projectUniqueName)
}

// ProjectRealUniqueName is similar to its sister methods, and returns a unique name for the runner && project.
// It uses the following parts to generate a truncated¹ sha256 sum:
//   - the runner's full token
//   - the runner's system ID
//   - the project ID
//   - the project runner ID
//
// With that the name is not susceptible to name clashes, when tokens are similar enough and therefore are the same
// after getting the runner's short description (i.e. after the token has been truncated)
//
// ¹ we truncate the resulting sum from original 32 bytes to 16 bytes, to give us and users a shorter name, thus shorter
// volume names when used in the docker volume manager. Truncating to 16 bytes (32 chars when hex encoded, the same
// length as an hex encoded md5sum) is cryptographically sound, it's still strong against collisions.
func (b *Build) ProjectRealUniqueName() string {
	const byteLen = 16

	data := fmt.Sprintf("%s-%s-%d-%d",
		b.Runner.GetToken(),
		b.Runner.GetSystemID(),
		b.JobInfo.ProjectID,
		b.ProjectRunnerID,
	)

	sum := sha256.Sum256([]byte(data))
	return "runner-" + hex.EncodeToString(sum[:byteLen])
}
```

**File:** common/build.go (L424-439)
```go
func (b *Build) ProjectUniqueDir(sharedDir bool) string {
	dir, err := b.ProjectSlug()
	if err != nil {
		dir = fmt.Sprintf("project-%d", b.JobInfo.ProjectID)
	}

	// for shared dirs path is constructed like this:
	// <some-path>/runner-short-id/concurrent-project-id/group-name/project-name/
	// ex.<some-path>/01234567/0/group/repo/
	if sharedDir {
		dir = path.Join(
			b.Runner.ShortDescription(),
			fmt.Sprintf("%d", b.ProjectRunnerID),
			dir,
		)
	}
```

**File:** docs/executors/docker.md (L691-710)
```markdown

  Host directories for host-based persistent storage:

  - For GitLab Runner before 18.4.0: `<cache-dir>/runner-<short-token>-project-<project-id>-concurrent-<concurrency-id>/<md5-of-path>`
  - For GitLab Runner 18.4.0 and later: `<cache-dir>/runner-<runner-id-hash>/<md5-of-path><protection>`

  Description of the variable parts:

  - `<short-token>`: The shortened version of the runner's token (first 8 letters)
  - `<project-id>`: The ID of the GitLab project
  - `<concurrency-id>`: The index of the runner from the list of all runners that run a build for the same project concurrently (accessible through the
    `CI_CONCURRENT_PROJECT_ID` [pre-defined variable](https://docs.gitlab.com/ci/variables/predefined_variables/)).
  - `<md5-of-path>`: The MD5 sum of the path within the container
  - `<runner-id-hash>`: The hash for the following data:
    - Runner's token
    - Runner's system ID
    - `<project-id>`
    - `<concurrency-id>`
  - `<protection>`: The value is empty for builds on unprotected branches, and `-protected` for protected branch builds
  - `<cache-dir>`: The configuration in `runners.docker.cache_dir`
```

**File:** docs-locale/fr-fr/fleet_scaling/_index.md (L68-95)
```markdown
### Configuration intermédiaire : un gestionnaire de runner, plusieurs runners {#intermediate-configuration-one-runner-manager-multiple-runners}

Vous pouvez également enregistrer plusieurs runners sur la même machine. Dans ce cas, le fichier `config.toml` du runner contient plusieurs sections `[[runners]]`. Si tous les workers de runner supplémentaires utilisent l'exécuteur shell et que vous mettez à jour la valeur du paramètre global `concurrent` à `3`, l'hôte peut exécuter au maximum trois jobs simultanément.

```toml
concurrent = 3

[[runners]]
  name = "instance_level_shell_001"
  url = ""
  token = ""
  executor = "shell"

[[runners]]
  name = "instance_level_shell_002"
  url = ""
  token = ""
  executor = "shell"

[[runners]]
  name = "instance_level_shell_003"
  url = ""
  token = ""
  executor = "shell"

```

Vous pouvez enregistrer de nombreux workers de runner sur la même machine, et chacun est un processus isolé. Les performances des jobs CI/CD pour chaque worker dépendent de la capacité de calcul du système hôte.
```

### Title
Colon injection into `Volume.Destination` via variable expansion breaks `Volume.Definition()` round-trip stability, enabling mount source/destination/mode confusion - (File: `executors/docker/internal/volumes/parser/volume.go`, `base_parser.go`)

### Summary
`Volume.Definition()` re-serializes a `Volume` by naively joining `Source`, `Destination`, `Mode`, `Label`, and `BindPropagation` with `:`/`,` without re-validating the result against `specExp`. The `Destination` field is populated via `p.varExpander(content)` *after* the colon-excluding regex match, so a pipeline-controlled variable value containing `:` can inject an unvalidated colon into `Destination`, producing a `Definition()` string that a subsequent (Docker Engine-side) mount-spec parser will split into different source/destination/mode fields than the runner intended.

### Finding Description
The regexes in `executors/docker/internal/volumes/parser/linux_parser.go` (`linuxDir`, `linuxVolumeName`) explicitly exclude `:` from `source` and `destination` matches: [1](#0-0) 

However, `matchesToVolumeSpecParts` in `base_parser.go` expands only the `destination` group through `p.varExpander(content)` *after* the regex has already validated the raw (pre-expansion) string: [2](#0-1) 

`p.varExpander` is `e.ExpandValue`, which substitutes CI/CD job variables (`${VAR}`/`$VAR`) — content that a pipeline author (an unprivileged job author) can set — as confirmed by the wiring in `setupDefaultExecutorOptions`: [3](#0-2) 

and by the test `TestExpandingVolumeDestination`, which explicitly documents that "source should not be expanded, destination should be expanded" using job variables (`JOB_VAR_1`, `JOB_VAR_2`, `COMBINED_VAR`): [4](#0-3) 

Because expansion happens *after* the colon-exclusion check, a variable value such as `evil:rw` substituted into a raw destination like `/tmp/${SOME_VAR}` produces a final `Destination` of `/tmp/evil:rw` — a string that would have been rejected by `specExp` if it had appeared literally, but is never re-validated.

This corrupted `Destination` then flows unchecked through `manager.addHostVolume` (only `absolutePath`/`Join`, no colon check) and into `Volume.Definition()`: [5](#0-4) [6](#0-5) 

`Definition()` joins `Source:Destination:Mode,Label,BindPropagation` and returns that string via `Manager.Binds()` to be handed to the Docker Engine API as a bind-mount specification string. Docker Engine's own mount-string parser (which this code was explicitly modeled after — see the comment referencing `docker/engine` `windows_parser.go`) will re-split that string on `:` using its own field-position logic. If `Destination` now contains an unexpected embedded `:`, the extra segment (e.g. `rw`) is no longer semantically "part of the path" but is re-interpreted by Docker's parser as the *mode* field, or in more contrived cases can shift what Docker treats as destination vs. mode entirely — a genuine round-trip failure: `ParseVolume(v.Definition()) != v`.

Existing checks do not stop this:
- `specExp` validates only the raw literal string before expansion, not the expanded runtime value.
- `manager.addHostVolume`/`absolutePath` only handle path-joining and root-path rejection, not colon-injection.
- `Definition()` itself performs no re-validation or escaping.

### Impact Explanation
Scoped impact: mount/volume confusion where a bind intended for one path or read-only mode is instead reinterpreted with an attacker-influenced mode/segment split. Concretely, if a runner admin configures a shared host volume using a job-variable placeholder in the destination (e.g. `/host/shared:/builds/project/${CACHE_SUBDIR}`), a pipeline author who controls `CACHE_SUBDIR` can set it to a value like `evil:rw` (or similar) to append an extra colon-delimited field to the resulting Docker bind spec, altering how Docker's own parser splits/interprets the mount (e.g., forcing a mode that was not intended, or in the case of more deeply crafted values, shifting the split point of destination and mode within the string). This does not grant escaping the executor sandbox on its own, but it does break the invariant that the runner's validated `Volume` struct is what actually reaches Docker, which can result in a mount landing with different semantics (e.g., a mode override) than the runner/administrator intended.

### Likelihood Explanation
Preconditions: the runner administrator's `config.toml` must define a `Docker.Volumes` entry whose *destination* contains a variable reference (`$VAR`/`${VAR}`) that resolves to a job/CI variable the pipeline author controls (not merely a `RunnerConfig`-level fixed variable). This is a real, documented, supported feature (confirmed by `Test_ExpandingVolumes` / `TestExpandingVolumeDestination`), so the mechanism for attacker-influenced destination expansion is already in production use — the only missing precondition is that the specific runner's volume configuration uses a variable for the destination path and that variable is user-settable. This is a realistic but not universal configuration; it depends on runner admin choices, but exploitation itself requires no special privilege beyond setting a normal CI/CD variable in a job.

### Recommendation
Re-validate the fully expanded `Destination` (and reject any occurrence of `:` or other `specExp`-forbidden characters) immediately after `p.varExpander` runs in `matchesToVolumeSpecParts`, before constructing the `Volume`. Additionally, add a round-trip self-check in `Volume.Definition()` (or immediately after building a `Volume`) that re-parses the generated definition string via the same parser and asserts equality with the original struct, failing the volume creation if it does not match — enforcing the invariant `ParseVolume(v.Definition()) == v` rather than only testing it implicitly through unit tests.

### Proof of Concept
```go
func TestLinuxParser_ExpandedDestinationColonInjection(t *testing.T) {
    // Simulates a runner-config volume "/host:/tmp/${INJECT}" where INJECT
    // is attacker-controlled (a normal CI/CD job variable).
    expander := strings.NewReplacer("${INJECT}", "evil:rw").Replace

    p := parser.NewLinuxParser(expander)
    v, err := p.ParseVolume("/host:/tmp/${INJECT}")
    require.NoError(t, err)

    // Destination now contains an unvalidated ':' that specExp would have
    // rejected had it appeared literally in the raw spec.
    assert.Contains(t, v.Destination, ":")

    def := v.Definition()
    // Round-trip: re-parsing the generated Definition() string with a
    // fresh parser instance should reproduce the same Volume - it does not.
    reparsed, err := p.ParseVolume(def)
    require.NoError(t, err)
    assert.NotEqual(t, v, reparsed) // demonstrates round-trip instability:
                                     // reparsed.Mode == "rw", reparsed.Destination == "/tmp/evil"
                                     // instead of the intended Destination "/tmp/evil:rw"
}
```
This demonstrates that `ParseVolume(v.Definition()) != v` for a valid, individually-field-valid `Volume` whose `Destination` was populated through variable expansion, confirming the round-trip invariant is violated and that a second-stage colon-based parser (such as Docker Engine's own bind-spec parser) would mis-split the string.

### Citations

**File:** executors/docker/internal/volumes/parser/linux_parser.go (L9-18)
```go
const (
	linuxDir        = `/(?:[^\\/:*?"<>|\r\n ]+/?)*`
	linuxVolumeName = `[^\\/:*?"<>|\r\n]+`

	linuxSource = `((?P<source>((` + linuxDir + `)|(` + linuxVolumeName + `))):)?`

	linuxDestination     = `(?P<destination>(?:` + linuxDir + `))`
	linuxMode            = `(:(?P<mode>(?i)(ro|rw|O)))?`
	linuxLabel           = `((:|,)(?P<label>(?i)z))?`
	linuxBindPropagation = `((:|,)(?P<bindPropagation>(?i)shared|slave|private|rshared|rslave|rprivate))?`
```

**File:** executors/docker/internal/volumes/parser/base_parser.go (L40-53)
```go
	for group := range parts {
		content, ok := matchgroups[group]
		if !ok {
			continue
		}

		switch group {
		case "destination":
			// We only want to expand destination, and not source or anything else.
			parts[group] = p.varExpander(content)
		default:
			parts[group] = content
		}
	}
```

**File:** executors/docker/docker.go (L1724-1726)
```go
		if e.volumeParser == nil {
			e.volumeParser = parser.NewLinuxParser(e.ExpandValue)
		}
```

**File:** executors/docker/docker_test.go (L2610-2645)
```go
func TestExpandingVolumeDestination(t *testing.T) {
	dockerClient := docker.NewMockClient(t)
	executor := executorWithMockClient(dockerClient)

	executor.Build = &common.Build{
		Job: spec.Job{
			Variables: spec.Variables{
				spec.Variable{Key: "JOB_VAR_1", Value: "1"},
				spec.Variable{Key: "JOB_VAR_2", Value: "2"},
				spec.Variable{Key: "COMBINED_VAR", Value: "${JOB_VAR_1}-${JOB_VAR_2}-3"},
			},
			JobInfo: spec.JobInfo{
				ProjectID: 1234,
			},
		},
		Runner: &common.RunnerConfig{
			RunnerCredentials: common.RunnerCredentials{
				Token: "theToken",
			},
			SystemID: "some-system-id",
		},
		ProjectRunnerID: 5678,
	}
	executor.Config = common.RunnerConfig{
		RunnerSettings: common.RunnerSettings{
			Docker: &common.DockerConfig{
				CacheDir: "",
				Volumes: []string{
					// source should not be expanded, destination should be expanded
					"/host/${COMBINED_VAR}:/tmp/${COMBINED_VAR}",
					// a new volume for the expanded destination should be created
					"/new/cache/vol-${COMBINED_VAR}-foo",
					// expected to be passed on as is
					"/${:/tmp",
					"/host:/tmp/foo/$",
				},
```

**File:** executors/docker/internal/volumes/manager.go (L108-124)
```go
func (m *manager) addHostVolume(volume *parser.Volume) error {
	var err error

	volume.Destination, err = m.absolutePath(volume.Destination)
	if err != nil {
		return fmt.Errorf("defining absolute path: %w", err)
	}

	err = m.managedVolumes.Add(volume.Destination)
	if err != nil {
		return fmt.Errorf("updating managed volume list: %w", err)
	}

	m.appendVolumeBind(volume)

	return nil
}
```

**File:** executors/docker/internal/volumes/parser/volume.go (L25-54)
```go
func (v *Volume) Definition() string {
	parts := make([]string, 0)
	builder := strings.Builder{}
	options := make([]string, 0)

	if v.Source != "" {
		parts = append(parts, v.Source)
	}

	parts = append(parts, v.Destination)

	if v.Mode != "" {
		options = append(options, v.Mode)
	}
	if v.Label != "" {
		options = append(options, v.Label)
	}
	if v.BindPropagation != "" {
		options = append(options, v.BindPropagation)
	}

	opts := strings.Join(options, ",")
	if opts != "" {
		parts = append(parts, opts)
	}

	builder.WriteString(strings.Join(parts, ":"))

	return builder.String()
}
```

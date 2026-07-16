// Package engine contains the immutable authority bundle for the AI Org body
// registry.  It is deliberately a sibling of the frozen root-v1 authority:
// importing this package cannot widen cuecodec's cue/schema or cue/records
// source roots.
package engine

import "embed"

// Authority contains only checked-in registry and schema sources.  The
// kindsv2 constructor hashes and evaluates these exact bytes; lifecycle target
// repositories never provide schema authority.
//
//go:embed registry/*.json schema/*.cue
var Authority embed.FS

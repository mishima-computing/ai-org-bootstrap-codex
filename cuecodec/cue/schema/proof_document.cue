package schema

// #ProofDocument is the first authoritative, closed kind admitted by the
// codec. Its numeric fields intentionally exercise values that cannot be
// represented without loss by an IEEE-754 float64 carrier.
#ProofDocument: close({
	// Exact identity is enforced by the Go boundary before unification. The
	// schema keeps these as strings so JSON Schema generation stays inside the
	// structured-output-v1 vocabulary (which intentionally excludes `const`).
	apiVersion!: string
	kind!:       string
	stable_id!:  string

	metadata!: close({
		title!:  string
		labels!: [...string]
	})

	spec!: close({
		enabled!:       bool
		large_integer!: int
		exact_decimal!: number
		exponent!:      number
		ordered_steps!: [...close({
			name!:   string
			weight!: int
		})]
		nested!: close({
			owner!: close({
				name!:   string
				active!: bool
			})
			rank_sequence!: [...int]
		})
	})
})

library QueryLet version '1.0.0'

// Query `let` clause (LANG-QUERY-LET): named bindings in QUERY-ITERATION scope. Each `let` is computed
// per source row and may reference the source alias AND earlier lets (dependency-ordered); `where`,
// `return`, and `sort` then reference them (an ELM QueryLetRef). Isolated at the System level (no FHIR)
// so the mechanics are visible in the emitted SQL. Mirrors a medication-period helper's shape
// (a let chain feeding a conditional return), which is the reason this feature exists.

// A let chain over a LIST source: `doubled` reads the row alias N; `plusOne` reads the earlier let.
define "LetChain":
  ({ 1, 2, 3 }) N
    let doubled: N * 2,
        plusOne: doubled + 1
    return plusOne

// A let referenced in BOTH `where` and `return`.
define "LetInWhere":
  ({ 10, 20, 30 }) N
    let half: N / 2
    where half > 7
    return half

// A let chain feeding a CONDITIONAL return — a medication-period helper's return shape
// (let startDate: …, total: … return if startDate is not null then Interval[startDate, …] else null),
// reduced to System integers over a list source.
define "LetConditionalReturn":
  ({ 4, 5 }) N
    let lo: N,
        hi: lo + 2
    return if lo > 4 then Interval[lo, hi] else null

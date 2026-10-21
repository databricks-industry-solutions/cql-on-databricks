library Unsupported version '1.0.0'

// A fixture that DELIBERATELY uses an operator the IR does not model yet, to keep the
// loud-failure path honest (CORE-NO-SILENT-MISLOWER): the unmodeled node must become an
// `UnknownExpr` at Load and trip the OnlyKernelNodes verifier at Normalize — never be
// silently lowered to a plausible-but-wrong plan. `collapse` (a list-of-intervals operator)
// is out of current scope; replace it with another unmodeled operator if it is ever modeled.

define "Collapsed": collapse { Interval[1, 3], Interval[2, 5] }

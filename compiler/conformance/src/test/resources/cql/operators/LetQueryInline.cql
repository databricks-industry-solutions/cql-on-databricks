library LetQueryInline version '1.0.0'

// transformation-catalog.md section 3 (LANG-QUERY, ARCH-TYPE-ATTR): a CORRELATED singleton-source
// query `(e) a return body` — a query whose source is a single scalar value that references an
// ENCLOSING alias — is eliminated in Normalize by substitution (`body[a := e]`), collapsing the
// nested query (LetQueryInliner). Here the inner `(N * 2) d return d + 1` is a scalar source that
// references the outer alias `N`, so it inlines to `(N * 2) + 1`, leaving "Scaled" with a single
// (outer) Query node.

define "Scaled": ({ 1, 2, 3 }) N return ((N * 2) d return d + 1)

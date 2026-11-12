library Strings version '1.0.0'

// The CQL string operators (LANG-STRING), each on ordinary input, the empty string and null, including a null
// computed by another expression. The Java CQF reference decides each operator's null and edge behaviour: case
// sensitivity, Upper and Lower outside ASCII, a combining mark, and characters outside the Basic Multilingual
// Plane. Length of a computed null is left out: the reference raises an error for it, which a fixture cannot
// declare (language-semantics.md section 13).

define "UpperOrdinary": Upper('Hello World')
define "UpperEmpty": Upper('')
define "UpperNull": Upper(null as String)
define "UpperNullFromExpression": Upper(Lower(null as String))
define "UpperSharpS": Upper('straße')
define "UpperLigature": Upper('ﬁ')
define "UpperCompared": Upper('abc') = 'ABC'

define "LowerOrdinary": Lower('Hello World')
define "LowerEmpty": Lower('')
define "LowerNull": Lower(null as String)
define "LowerNullFromExpression": Lower(Upper(null as String))
define "LowerAccented": Lower('É')
define "LowerDottedCapitalI": Lower('İ')
define "LowerFinalSigma": Lower('ΟΔΟΣ')

define "LengthOrdinary": Length('Hello World')
define "LengthEmpty": Length('')
define "LengthNull": Length(null as String)
define "LengthOutsideBmp": Length('😀')
define "LengthCombiningMark": Length('e\u0301')
define "LengthPlusOne": Length('Hello') + 1

define "StartsWithTrue": StartsWith('Hello World', 'Hello')
define "StartsWithFalse": StartsWith('Hello World', 'World')
define "StartsWithCaseSensitive": StartsWith('Hello World', 'hello')
define "StartsWithEmptyPrefix": StartsWith('Hello World', '')
define "StartsWithOnEmpty": StartsWith('', 'Hello')
define "StartsWithEmptyOnEmpty": StartsWith('', '')
define "StartsWithNullArgument": StartsWith(null as String, 'Hello')
define "StartsWithNullPrefix": StartsWith('Hello World', null as String)
define "StartsWithBothNull": StartsWith(null as String, null as String)
define "StartsWithNullFromExpression": StartsWith(Lower(null as String), 'Hello')

define "EndsWithTrue": EndsWith('Hello World', 'World')
define "EndsWithFalse": EndsWith('Hello World', 'Hello')
define "EndsWithCaseSensitive": EndsWith('Hello World', 'WORLD')
define "EndsWithEmptySuffix": EndsWith('Hello World', '')
define "EndsWithOnEmpty": EndsWith('', 'World')
define "EndsWithEmptyOnEmpty": EndsWith('', '')
define "EndsWithOutsideBmp": EndsWith('a😀', '😀')
define "EndsWithNullArgument": EndsWith(null as String, 'World')
define "EndsWithNullSuffix": EndsWith('Hello World', null as String)
define "EndsWithNullFromExpression": EndsWith('Hello World', Upper(null as String))

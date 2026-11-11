library Strings version '1.0.0'

// The CQL string operators (LANG-STRING), each on ordinary input, the empty string and null. The Java CQF
// reference decides each operator's null and edge behaviour, including Length of a character outside the
// Basic Multilingual Plane.

define "UpperOrdinary": Upper('Hello World')
define "UpperEmpty": Upper('')
define "UpperNull": Upper(null as String)

define "LowerOrdinary": Lower('Hello World')
define "LowerEmpty": Lower('')
define "LowerNull": Lower(null as String)

define "LengthOrdinary": Length('Hello World')
define "LengthEmpty": Length('')
define "LengthNull": Length(null as String)
define "LengthOutsideBmp": Length('😀')

define "StartsWithTrue": StartsWith('Hello World', 'Hello')
define "StartsWithFalse": StartsWith('Hello World', 'World')
define "StartsWithEmptyPrefix": StartsWith('Hello World', '')
define "StartsWithOnEmpty": StartsWith('', 'Hello')
define "StartsWithNullArgument": StartsWith(null as String, 'Hello')
define "StartsWithNullPrefix": StartsWith('Hello World', null as String)

define "EndsWithTrue": EndsWith('Hello World', 'World')
define "EndsWithFalse": EndsWith('Hello World', 'Hello')
define "EndsWithEmptySuffix": EndsWith('Hello World', '')
define "EndsWithOnEmpty": EndsWith('', 'World')
define "EndsWithNullArgument": EndsWith(null as String, 'World')
define "EndsWithNullSuffix": EndsWith('Hello World', null as String)

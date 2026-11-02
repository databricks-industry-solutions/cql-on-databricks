library DecimalStep version '1.0.0'

// EXEC-SQL-DECIMAL-STEP (execution-model.md): Predecessor, Successor and HighBoundary over Decimal step
// and pad in the 8th decimal place, so each result keeps that digit.

define "PredecessorOfOne":   predecessor of 1.0          // 0.99999999
define "SuccessorOfOne":     successor of 1.0            // 1.00000001
define "HighBoundaryDecimal": HighBoundary(1.587, 8)     // 1.58799999
